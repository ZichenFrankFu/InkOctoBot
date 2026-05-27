"""Chapter snapshot writer — captures full prompt context + diagnostics
into ``chapter_snapshots`` at commit time.

This is called inline from :func:`pipeline.fire_and_forget_from_handler`
BEFORE the async sub-tasks run, so a snapshot row exists by the time
historical-view queries hit. Hash-dedupes large config blobs into
``chapter_config_snapshots`` so 100 commits of an unchanged worldbook
don't store the worldbook 100 times.

Spec § 模块 8 Option B (full version history).
"""
from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import uuid
from typing import Any

logger = logging.getLogger("inkoctobot.commit_pipeline.snapshot_writer")


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:24]


def _store_config(con, config_type: str, content: str) -> str:
    """Hash-deduped INSERT — returns the config_hash to be referenced
    from chapter_snapshots."""
    if not content:
        return ""
    h = _sha(content)
    con.execute(
        "INSERT OR IGNORE INTO chapter_config_snapshots "
        "(config_hash, config_type, full_content) "
        "VALUES (?, ?, ?)",
        (h, config_type, content),
    )
    return h


def _collect_chapter_fields(db_path: str, project_id: str, chapter_id: str) -> dict:
    """Pull chapter-local fields without breaking on missing optional cols."""
    try:
        from ui.backend.app.services.prompt_context.chapter_fields import (
            load_chapter_fields,
        )
        return load_chapter_fields(project_id, chapter_id) or {}
    except Exception as e:
        logger.debug("load_chapter_fields failed: %s", e)
        return {}


def _collect_project_blob(db_path: str, project_id: str) -> str:
    try:
        with sqlite3.connect(db_path) as con:
            row = con.execute(
                "SELECT title, genre, logline, status, current_chapter, "
                "       style_profile_json, model_preset "
                "FROM projects WHERE project_id = ?",
                (project_id,),
            ).fetchone()
        return json.dumps({
            "title": row[0], "genre": row[1], "logline": row[2],
            "status": row[3], "current_chapter": row[4],
            "style_profile_json": row[5], "model_preset": row[6],
        }, ensure_ascii=False) if row else "{}"
    except Exception:
        return "{}"


def _collect_characters_blob(db_path: str, project_id: str) -> str:
    try:
        with sqlite3.connect(db_path) as con:
            rows = con.execute(
                "SELECT name, personality, speech_style "
                "FROM characters WHERE project_id = ?",
                (project_id,),
            ).fetchall()
        return json.dumps([
            {"name": r[0], "personality": r[1], "speech_style": r[2]}
            for r in rows
        ], ensure_ascii=False)
    except Exception:
        return "[]"


def _collect_worldbook_blob(db_path: str, project_id: str) -> str:
    try:
        with sqlite3.connect(db_path) as con:
            rows = con.execute(
                "SELECT title, category, content FROM worldbook_entries "
                "WHERE project_id = ?",
                (project_id,),
            ).fetchall()
        return json.dumps([
            {"title": r[0], "category": r[1], "content": r[2]}
            for r in rows
        ], ensure_ascii=False)
    except Exception:
        return "[]"


def _build_full_prompt_and_diagnostics(
    project_id: str, chapter_id: str, db_path: str,
) -> tuple[str, str]:
    """Re-build the prompt context exactly as the writer saw it, plus
    capture the diagnostics dict. Failures degrade to empty strings
    so the snapshot row still gets created with whatever's available."""
    try:
        from ui.backend.app.services.prompt_context.builder import (
            build_generation_context,
        )
        # Resolve chapter_num from chapters table.
        chapter_num = 0
        with sqlite3.connect(db_path) as con:
            row = con.execute(
                "SELECT chapter_num FROM chapters WHERE chapter_id = ?",
                (chapter_id,),
            ).fetchone()
            if row:
                chapter_num = int(row[0]) if row[0] else 0
        ctx = build_generation_context(
            project_id=project_id, chapter_num=chapter_num,
            chapter_id=chapter_id, db_path=db_path,
        )
        # Concatenate non-empty blocks for the full_prompt snapshot.
        prompt = "\n".join(v for v in ctx.get("blocks", {}).values() if v)
        diag = json.dumps(ctx.get("diagnostics", {}), ensure_ascii=False)
        return prompt, diag
    except Exception as e:
        logger.debug("snapshot prompt rebuild skipped: %s", e)
        return "", "{}"


def capture_snapshot(
    db_path: str, project_id: str, chapter_id: str, chapter_num: int,
) -> str:
    """Write one row into ``chapter_snapshots`` representing the
    commit's full context. Returns the snapshot_id.

    Hash-dedupes the bulky project / characters / worldbook blobs into
    ``chapter_config_snapshots`` so they're stored once across all
    commits that share the same config.
    """
    fields = _collect_chapter_fields(db_path, project_id, chapter_id)
    proj_blob = _collect_project_blob(db_path, project_id)
    chars_blob = _collect_characters_blob(db_path, project_id)
    wb_blob = _collect_worldbook_blob(db_path, project_id)

    snap_id = f"snap_{uuid.uuid4().hex[:12]}"
    full_prompt, diagnostics = _build_full_prompt_and_diagnostics(
        project_id, chapter_id, db_path,
    )

    try:
        from ui.backend.app.services.embedding import get_embedding_service
        embedding_model = get_embedding_service().get_current_model().model_key
    except Exception:
        embedding_model = ""

    with sqlite3.connect(db_path) as con:
        proj_hash = _store_config(con, "project", proj_blob)
        chars_hash = _store_config(con, "characters", chars_blob)
        wb_hash = _store_config(con, "worldbook", wb_blob)

        con.execute(
            "INSERT INTO chapter_snapshots "
            "(snapshot_id, project_id, chapter_id, chapter_num, "
            " chapter_outline_snapshot, on_stage_entities_snapshot, "
            " special_requirements_snapshot, project_config_hash, "
            " characters_config_hash, worldbook_hash, "
            " full_prompt_snapshot, diagnostics_snapshot, "
            " embedding_model_used) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (snap_id, project_id, chapter_id, chapter_num,
             (fields.get("synopsis") or ""),
             json.dumps(fields.get("on_stage_entities") or {}, ensure_ascii=False),
             (fields.get("special_requirements") or ""),
             proj_hash, chars_hash, wb_hash,
             full_prompt, diagnostics, embedding_model),
        )
        con.commit()
    return snap_id


def list_snapshots_for_chapter(db_path: str, chapter_id: str) -> list[dict]:
    with sqlite3.connect(db_path) as con:
        con.row_factory = sqlite3.Row
        rows = con.execute(
            "SELECT snapshot_id, chapter_id, chapter_num, committed_at, "
            "       generation_model_used, embedding_model_used "
            "FROM chapter_snapshots WHERE chapter_id = ? "
            "ORDER BY committed_at DESC",
            (chapter_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_snapshot(db_path: str, snapshot_id: str) -> dict[str, Any] | None:
    with sqlite3.connect(db_path) as con:
        con.row_factory = sqlite3.Row
        row = con.execute(
            "SELECT * FROM chapter_snapshots WHERE snapshot_id = ?",
            (snapshot_id,),
        ).fetchone()
    return dict(row) if row else None
