"""SkillIndex — DB mirror of the filesystem SkillRegistry.

LOADER_SPEC Loader 14 prerequisite. The filesystem SkillRegistry stays
authoritative for *which* skills exist; this DB-side index carries:

- the small set of fields the prompt-context loader needs to render
  (display_name + description + body_snippet)
- per-skill embeddings so the loader can rank by cosine without
  re-embedding on every prompt build
- per-project pin set so user choices survive across registry rescans

Lifecycle:

- ``sync_from_registry(db_path)`` rebuilds the mirror to match the
  current in-memory SkillRegistry. Skills that vanished from disk are
  removed; new ones are inserted. Existing rows keep their embeddings
  iff the canonical text (display_name + description + body_snippet)
  is unchanged — otherwise the embedding is cleared so the loader
  recomputes lazily.
"""
from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import time
from typing import Any

from .project_store import open_db, _row_to_dict

logger = logging.getLogger("inkoctobot.services.skill_index")


_BODY_SNIPPET_CHARS = 1200


# Track which DB paths have been synced this process so we don't rebuild
# the mirror on every build_generation_context call. The skill registry
# itself is reloaded only on explicit user action.
_synced_paths: set[str] = set()


def skill_embedding_text(row: dict) -> str:
    """Canonical text used for the per-row embedding hash."""
    parts = [
        (row.get("display_name") or "").strip(),
        (row.get("description") or "").strip(),
        (row.get("body_snippet") or "").strip(),
    ]
    return "\n".join(p for p in parts if p)


def hash_embedding_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _row_to_payload(row: sqlite3.Row, *, include_embedding: bool = False) -> dict:
    r = _row_to_dict(row)
    out: dict[str, Any] = {
        "skill_id":     r["skill_id"],
        "display_name": r["display_name"],
        "description":  r.get("description") or "",
        "kind":         r.get("kind") or "builtin",
        "body_snippet": r.get("body_snippet") or "",
        "updated_at":   r.get("updated_at"),
    }
    if include_embedding:
        try:
            out["embedding"] = json.loads(r.get("embedding_json") or "[]")
        except (TypeError, json.JSONDecodeError):
            out["embedding"] = []
        out["embedding_text_hash"] = r.get("embedding_text_hash") or ""
        out["embedding_model_key"] = r.get("embedding_model_key") or ""
    return out


# ─────────── CRUD ───────────


def list_skills(db_path: str, *, include_embedding: bool = False) -> list[dict]:
    with open_db(db_path) as con:
        rows = con.execute(
            "SELECT * FROM skill_index ORDER BY display_name"
        ).fetchall()
    return [_row_to_payload(r, include_embedding=include_embedding) for r in rows]


def get_skill(db_path: str, skill_id: str, *,
              include_embedding: bool = False) -> dict | None:
    with open_db(db_path) as con:
        row = con.execute(
            "SELECT * FROM skill_index WHERE skill_id = ?",
            (skill_id,),
        ).fetchone()
    return _row_to_payload(row, include_embedding=include_embedding) if row else None


def upsert_skill(db_path: str, body: dict) -> dict:
    """Upsert a single skill mirror row.

    Preserves the existing embedding when the canonical text didn't
    change; clears it otherwise so the loader recomputes lazily.
    """
    sid = body["skill_id"]
    new_text = skill_embedding_text(body)
    new_hash = hash_embedding_text(new_text)
    with open_db(db_path) as con:
        prev = con.execute(
            "SELECT embedding_json, embedding_text_hash FROM skill_index "
            "WHERE skill_id = ?",
            (sid,),
        ).fetchone()
        if prev is not None and (prev["embedding_text_hash"] or "") == new_hash:
            keep_embed = prev["embedding_json"] or "[]"
            keep_hash = new_hash
        else:
            keep_embed = "[]"
            keep_hash = ""

        con.execute(
            """INSERT INTO skill_index (
                   skill_id, display_name, description, kind, body_snippet,
                   embedding_json, embedding_text_hash, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
               ON CONFLICT(skill_id) DO UPDATE SET
                   display_name = excluded.display_name,
                   description = excluded.description,
                   kind = excluded.kind,
                   body_snippet = excluded.body_snippet,
                   embedding_json = excluded.embedding_json,
                   embedding_text_hash = excluded.embedding_text_hash,
                   updated_at = CURRENT_TIMESTAMP""",
            (sid, body.get("display_name") or sid,
             body.get("description") or "",
             body.get("kind") or "builtin",
             (body.get("body_snippet") or "")[:_BODY_SNIPPET_CHARS],
             keep_embed, keep_hash),
        )
        con.commit()
    out = get_skill(db_path, sid)
    if out is None:  # pragma: no cover
        raise RuntimeError("upsert succeeded but row not found")
    return out


def delete_skill(db_path: str, skill_id: str) -> None:
    with open_db(db_path) as con:
        con.execute("DELETE FROM skill_index WHERE skill_id = ?", (skill_id,))
        con.commit()


def set_embedding(
    db_path: str, skill_id: str,
    embedding: list[float], text_hash: str,
    model_key: str = "",
) -> None:
    with open_db(db_path) as con:
        con.execute(
            "UPDATE skill_index SET embedding_json = ?, embedding_text_hash = ?, "
            "embedding_model_key = ? WHERE skill_id = ?",
            (json.dumps(list(embedding), ensure_ascii=False),
             text_hash, model_key or "", skill_id),
        )
        con.commit()


# ─────────── pins ───────────


def pin_skill(db_path: str, project_id: str, skill_id: str) -> None:
    with open_db(db_path) as con:
        con.execute(
            "INSERT OR IGNORE INTO project_skill_pins "
            "(project_id, skill_id) VALUES (?, ?)",
            (project_id, skill_id),
        )
        con.commit()


def unpin_skill(db_path: str, project_id: str, skill_id: str) -> None:
    with open_db(db_path) as con:
        con.execute(
            "DELETE FROM project_skill_pins WHERE project_id = ? AND skill_id = ?",
            (project_id, skill_id),
        )
        con.commit()


def list_pinned_skill_ids(db_path: str, project_id: str) -> list[str]:
    with open_db(db_path) as con:
        rows = con.execute(
            "SELECT skill_id FROM project_skill_pins WHERE project_id = ?",
            (project_id,),
        ).fetchall()
    return [r["skill_id"] for r in rows]


# ─────────── sync from filesystem registry ───────────


def _registry_snapshot() -> list[dict]:
    """Pull (skill_id, display_name, description, kind, body_snippet) from
    the in-memory SkillRegistry. Returns ``[]`` if the registry isn't
    available (test environments without filesystem skills, etc.).
    """
    try:
        from ui.backend.app.routers.skill_api import (
            _get_registry, _skill_public_dict, _get_deactivated,
        )
        registry = _get_registry()
        deactivated = _get_deactivated()
    except Exception as e:
        logger.debug("skill registry unavailable: %s", e)
        return []
    out: list[dict] = []
    for skill in registry._skills.values():
        try:
            info = _skill_public_dict(skill, deactivated)
        except Exception:
            continue
        sid = (info.get("name") or "").strip()
        if not sid:
            continue
        body = info.get("body") or info.get("source") or ""
        out.append({
            "skill_id":     sid,
            "display_name": info.get("display_name") or sid,
            "description":  info.get("description") or "",
            "kind":         "learned" if info.get("is_learned") else "builtin",
            "body_snippet": body[:_BODY_SNIPPET_CHARS],
        })
    return out


def sync_from_registry(db_path: str, *, force: bool = False) -> int:
    """Reconcile the DB mirror with the current filesystem registry.

    Returns the number of rows upserted. ``force=True`` bypasses the
    once-per-process cache. Rows for skills missing from disk are
    removed (the FK cascade on ``project_skill_pins`` cleans up too).
    """
    if not force and db_path in _synced_paths:
        return 0
    snapshot = _registry_snapshot()
    desired_ids = {s["skill_id"] for s in snapshot}
    n = 0
    for entry in snapshot:
        upsert_skill(db_path, entry)
        n += 1
    # Remove mirror rows that disappeared from disk. DB-native rows
    # (kind='knowledge' — 专业知识 skill, spec 4.2) have no filesystem
    # counterpart and must survive the reconcile.
    with open_db(db_path) as con:
        rows = con.execute(
            "SELECT skill_id, kind FROM skill_index",
        ).fetchall()
        for r in rows:
            if r["skill_id"] not in desired_ids and \
                    (r["kind"] or "") != "knowledge":
                con.execute(
                    "DELETE FROM skill_index WHERE skill_id = ?",
                    (r["skill_id"],),
                )
        con.commit()
    _synced_paths.add(db_path)
    logger.info("skill_index sync_from_registry upserted=%d", n)
    return n


def reset_sync_cache() -> None:
    """Test helper — clear the once-per-process sync guard."""
    _synced_paths.clear()
