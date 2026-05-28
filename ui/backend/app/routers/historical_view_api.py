"""Historical view API (spec § 模块 10).

Five tabs reconstruct what the writer "saw" when the chapter was
committed:

- Tab 1 — chapter_segments (paragraph-source view of the text itself)
- Tab 2 — storyland_state at K (rebuild from state_change_log)
- Tab 3 — reader_memory snapshot at K-1 (uses the loader directly)
- Tab 4 — full_prompt_snapshot from chapter_snapshots
- Tab 5 — diagnostics_snapshot from chapter_snapshots

The 5-tab data is returned as one combined JSON to minimize round
trips; the UI can lazy-load any individual tab via the per-tab GET.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from ..services.commit_pipeline.snapshot_writer import (
    get_snapshot, list_snapshots_for_chapter,
)
from ..services.project_paths import get_db_path

logger = logging.getLogger("inkoctobot.routers.historical_view_api")


router = APIRouter(prefix="/api/historical-view", tags=["historical-view"])


def _resolve_chapter_num(db_path: str, chapter_id: str) -> int | None:
    with sqlite3.connect(db_path) as con:
        row = con.execute(
            "SELECT chapter_num FROM chapters WHERE chapter_id = ?",
            (chapter_id,),
        ).fetchone()
    return int(row[0]) if row and row[0] else None


# ─────────── Tab 1: chapter segments ───────────


def _tab1_content(db_path: str, chapter_id: str) -> dict:
    with sqlite3.connect(db_path) as con:
        con.row_factory = sqlite3.Row
        rows = con.execute(
            "SELECT segment_id, sequence_order, content, source "
            "FROM chapter_segments WHERE chapter_id = ? "
            "ORDER BY sequence_order",
            (chapter_id,),
        ).fetchall()
    return {"segments": [dict(r) for r in rows]}


# ─────────── Tab 2: storyland state at K ───────────


def _tab2_state_at_chapter(
    db_path: str, project_id: str, chapter_num: int,
) -> dict:
    """Reconstruct the active (subject, predicate) state set at chapter
    K from state_change_log — the latest non-cancellation change at or
    before K for each (subject, predicate) pair wins."""
    with sqlite3.connect(db_path) as con:
        # SQLite window-function support landed in 3.25; use a
        # correlated subquery for older versions.
        rows = con.execute(
            "SELECT subject, subject_type, predicate, new_value, "
            "       change_chapter, trigger "
            "FROM state_change_log s1 "
            "WHERE s1.project_id = ? AND s1.change_chapter <= ? "
            "  AND s1.new_value IS NOT NULL "
            "  AND s1.recorded_at = ("
            "      SELECT MAX(s2.recorded_at) FROM state_change_log s2 "
            "      WHERE s2.project_id = s1.project_id "
            "        AND s2.subject = s1.subject "
            "        AND s2.predicate = s1.predicate "
            "        AND s2.change_chapter <= ? "
            "  ) "
            "ORDER BY subject, predicate",
            (project_id, chapter_num, chapter_num),
        ).fetchall()
    return {
        "chapter_num": chapter_num,
        "active_state": [
            {"subject": r[0], "subject_type": r[1], "predicate": r[2],
             "value": r[3], "from_chapter": r[4], "trigger": r[5]}
            for r in rows
        ],
    }


# ─────────── Tab 3: reader memory at K-1 ───────────


def _tab3_reader_memory(
    project_id: str, chapter_id: str, chapter_num: int,
) -> dict:
    """Call the reader_memory loader at K-1 — that's "what the reader
    knew before the chapter was written"."""
    try:
        from ..services.prompt_context.loaders import reader_memory
        plan = reader_memory.plan(
            project_id, max(1, chapter_num),  # loader gates internally
        )
        body = plan.render(plan.target) if plan else ""
        return {"chapter_num": chapter_num, "rendered": body}
    except Exception as e:
        logger.debug("reader_memory loader failed: %s", e)
        return {"chapter_num": chapter_num, "rendered": "", "error": str(e)}


# ─────────── Tab 4 + 5: from snapshot ───────────


def _tab4_5_from_snapshot(snapshot: dict) -> tuple[dict, dict]:
    full_prompt = snapshot.get("full_prompt_snapshot") or ""
    try:
        diag = json.loads(snapshot.get("diagnostics_snapshot") or "{}")
    except json.JSONDecodeError:
        diag = {}
    tab4 = {
        "full_prompt":       full_prompt,
        "embedding_model":   snapshot.get("embedding_model_used") or "",
        "generation_model":  snapshot.get("generation_model_used") or "",
    }
    return tab4, diag


# ─────────── public endpoints ───────────


@router.get("/snapshots/{chapter_id}")
def list_snapshots(chapter_id: str) -> dict:
    """List every snapshot for a chapter (the picker in the UI)."""
    return {
        "chapter_id": chapter_id,
        "snapshots":  list_snapshots_for_chapter(get_db_path(), chapter_id),
    }


@router.get("/{chapter_id}")
def get_historical_view(
    chapter_id: str,
    snapshot_id: str | None = Query(default=None),
) -> dict:
    """One-shot fetch of all 5 tabs. ``snapshot_id`` picks a specific
    historical commit; default = latest snapshot for the chapter."""
    db = get_db_path()
    chapter_num = _resolve_chapter_num(db, chapter_id)
    if chapter_num is None:
        raise HTTPException(404, f"chapter {chapter_id!r} not found")

    # Resolve snapshot.
    snapshots = list_snapshots_for_chapter(db, chapter_id)
    if snapshot_id is None and snapshots:
        snapshot_id = snapshots[0]["snapshot_id"]
    snap = get_snapshot(db, snapshot_id) if snapshot_id else None

    # Project id for state_change_log lookup.
    with sqlite3.connect(db) as con:
        proj_row = con.execute(
            "SELECT project_id FROM chapters WHERE chapter_id = ?",
            (chapter_id,),
        ).fetchone()
    project_id = proj_row[0] if proj_row else ""

    tab4, tab5 = _tab4_5_from_snapshot(snap or {})

    return {
        "chapter_id":           chapter_id,
        "chapter_num":          chapter_num,
        "snapshot_id":          snapshot_id,
        "tab_1_content":        _tab1_content(db, chapter_id),
        "tab_2_storyland_state": _tab2_state_at_chapter(db, project_id, chapter_num),
        "tab_3_reader_memory":  _tab3_reader_memory(
                                    project_id, chapter_id,
                                    max(1, chapter_num - 1)),
        "tab_4_full_prompt":    tab4,
        "tab_5_diagnostics":    tab5,
    }
