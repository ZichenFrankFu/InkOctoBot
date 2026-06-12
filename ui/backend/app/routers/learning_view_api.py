"""Read-only views for the learning subsystem (Part A) + audit gate.

These endpoints back the new ``PreferencesPage`` and ``TruthReviewPage``
UI surfaces. They are intentionally lightweight: list current
preferences, list per-chapter audit status, list raw observations.
"""
from __future__ import annotations

import json
import logging
import sqlite3

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ui.backend.app.services.project_paths import get_db_path

router = APIRouter(prefix="/api/learning", tags=["learning-view"])
logger = logging.getLogger("inkoctobot.ui.backend.learning_view_api")


def _open() -> sqlite3.Connection:
    con = sqlite3.connect(get_db_path())
    con.row_factory = sqlite3.Row
    return con


# ─────────── Preferences (Part A) ───────────


@router.get("/preferences")
def list_preferences(project_id: str, min_confidence: float = 0.0) -> dict:
    """List all learned preferences for a project."""
    with _open() as con:
        rows = con.execute(
            """SELECT pref_id, preference_type, description,
                      confidence, observation_count, is_confirmed,
                      created_at, updated_at
               FROM user_style_preferences
               WHERE project_id = ? AND confidence >= ?
               ORDER BY confidence DESC, observation_count DESC""",
            (project_id, min_confidence),
        ).fetchall()
    return {"items": [dict(r) for r in rows]}


@router.delete("/preferences/{pref_id}")
def delete_preference(pref_id: str) -> dict:
    with _open() as con:
        con.execute("DELETE FROM user_style_preferences WHERE pref_id = ?",
                    (pref_id,))
        con.commit()
    return {"status": "ok"}


# ─────────── Edit observations ───────────


@router.get("/observations")
def list_observations(project_id: str, consumed: int = -1) -> dict:
    """List edit observations.

    ``consumed=-1`` (default) → all. ``consumed=0`` → not yet batch-
    processed. ``consumed=1`` → already absorbed into preferences."""
    q = ("SELECT obs_id, chapter_id, chapter_num, special_requirement, "
         "diff_stats_json, consumed, created_at "
         "FROM edit_observations WHERE project_id = ?")
    params: list = [project_id]
    if consumed in (0, 1):
        q += " AND consumed = ?"
        params.append(consumed)
    q += " ORDER BY created_at DESC LIMIT 200"
    with _open() as con:
        rows = con.execute(q, params).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["diff_stats"] = json.loads(d.get("diff_stats_json") or "{}")
        except json.JSONDecodeError:
            d["diff_stats"] = {}
        out.append(d)
    return {"items": out}


@router.get("/threshold-status")
def threshold_status(project_id: str) -> dict:
    """Return how close the project is to the next batch extraction."""
    try:
        from ui.backend.app.settings import get_edit_learning_threshold
        threshold = get_edit_learning_threshold()
    except Exception:
        threshold = 5
    with _open() as con:
        unconsumed = con.execute(
            "SELECT COUNT(*) FROM edit_observations "
            "WHERE project_id = ? AND consumed = 0",
            (project_id,),
        ).fetchone()[0]
    return {
        "threshold": int(threshold),
        "unconsumed": int(unconsumed),
        "remaining": max(0, int(threshold) - int(unconsumed)),
        "will_fire_on_next": int(unconsumed) + 1 >= int(threshold),
    }


class ThresholdUpdate(BaseModel):
    threshold: int


@router.post("/threshold")
def set_threshold(body: ThresholdUpdate) -> dict:
    from ui.backend.app.settings import set_edit_learning_threshold
    n = set_edit_learning_threshold(body.threshold)
    return {"threshold": n}


# ─────────── Truth audit (chapter-level) ───────────


@router.get("/chapter-audits")
def list_chapter_audits(project_id: str) -> dict:
    """List per-chapter audit status for the project — drives the
    Truth Review page."""
    with _open() as con:
        rows = con.execute(
            """SELECT chapter_id, chapter_num, title, audit_status,
                      audit_issues_json, updated_at
               FROM chapters WHERE project_id = ?
               ORDER BY chapter_num""",
            (project_id,),
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["audit_issues"] = json.loads(d.get("audit_issues_json") or "[]")
        except json.JSONDecodeError:
            d["audit_issues"] = []
        out.append(d)
    return {"items": out}


class OverrideRequest(BaseModel):
    chapter_id: str
    note: str = ""


@router.post("/override-audit")
def override_audit(body: OverrideRequest) -> dict:
    """Mark a failed-audit chapter as user-accepted."""
    with _open() as con:
        con.execute(
            """UPDATE chapters
               SET audit_status = 'audit_overridden',
                   updated_at = CURRENT_TIMESTAMP
               WHERE chapter_id = ?""",
            (body.chapter_id,),
        )
        con.commit()
    return {"status": "audit_overridden", "chapter_id": body.chapter_id}
