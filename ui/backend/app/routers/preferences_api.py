"""/api/preferences — 用户偏好画像 review surface (spec 4.1).

Preferences are learned in batches by the post-commit
``preference_analyzer`` sub-task and land as PENDING rows
(``is_confirmed=0``). This router is the confirmation gate
(用户偏好·机制4): list / confirm / edit / delete, plus a manual
trigger for the batch learner.
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

router = APIRouter(prefix="/api/preferences", tags=["preferences"])


def _db_path() -> str:
    from ui.backend.app.services.project_paths import get_db_path
    return get_db_path()


def _row_to_dict(r: sqlite3.Row) -> dict[str, Any]:
    d = dict(r)
    try:
        d["source_chapters"] = json.loads(d.pop("source_chapters_json", "[]") or "[]")
    except Exception:
        d["source_chapters"] = []
    d["is_confirmed"] = bool(d.get("is_confirmed"))
    return d


@router.get("")
def list_preferences(
    project_id: str = Query(...),
    status: str = Query(default="all", pattern="^(all|pending|confirmed)$"),
):
    """List the project's preference profile, grouped by type."""
    where = "project_id = ?"
    if status == "pending":
        where += " AND is_confirmed = 0"
    elif status == "confirmed":
        where += " AND is_confirmed = 1"
    with sqlite3.connect(_db_path()) as con:
        con.row_factory = sqlite3.Row
        try:
            rows = con.execute(
                f"SELECT pref_id, project_id, preference_type, description, "
                f"confidence, observation_count, is_confirmed, "
                f"source_chapters_json, source_type, created_at, updated_at "
                f"FROM user_style_preferences WHERE {where} "
                f"ORDER BY is_confirmed ASC, confidence DESC",
                (project_id,),
            ).fetchall()
        except sqlite3.OperationalError:
            return {"items": [], "pending_count": 0}
    items = [_row_to_dict(r) for r in rows]
    return {
        "items": items,
        "pending_count": sum(1 for i in items if not i["is_confirmed"]),
    }


@router.post("/{pref_id}/confirm")
def confirm_preference(pref_id: str):
    """User approves a learned preference — it now enters prompts."""
    with sqlite3.connect(_db_path()) as con:
        cur = con.execute(
            "UPDATE user_style_preferences SET is_confirmed = 1, "
            "updated_at = CURRENT_TIMESTAMP WHERE pref_id = ?",
            (pref_id,),
        )
        con.commit()
    if cur.rowcount == 0:
        raise HTTPException(404, "preference not found")
    return {"ok": True, "pref_id": pref_id, "is_confirmed": True}


@router.post("/{pref_id}/unconfirm")
def unconfirm_preference(pref_id: str):
    """Pull a preference back out of prompts without deleting it."""
    with sqlite3.connect(_db_path()) as con:
        cur = con.execute(
            "UPDATE user_style_preferences SET is_confirmed = 0, "
            "updated_at = CURRENT_TIMESTAMP WHERE pref_id = ?",
            (pref_id,),
        )
        con.commit()
    if cur.rowcount == 0:
        raise HTTPException(404, "preference not found")
    return {"ok": True, "pref_id": pref_id, "is_confirmed": False}


class PreferencePatch(BaseModel):
    description: str | None = None
    preference_type: str | None = None


@router.put("/{pref_id}")
def update_preference(pref_id: str, patch: PreferencePatch):
    sets: list[str] = []
    args: list[Any] = []
    if patch.description is not None:
        desc = patch.description.strip()
        if not desc:
            raise HTTPException(400, "description cannot be empty")
        sets.append("description = ?")
        args.append(desc)
    if patch.preference_type is not None:
        if patch.preference_type not in ("style", "content", "pacing"):
            raise HTTPException(400, "preference_type must be style/content/pacing")
        sets.append("preference_type = ?")
        args.append(patch.preference_type)
    if not sets:
        raise HTTPException(400, "nothing to update")
    sets.append("updated_at = CURRENT_TIMESTAMP")
    with sqlite3.connect(_db_path()) as con:
        cur = con.execute(
            f"UPDATE user_style_preferences SET {', '.join(sets)} "
            f"WHERE pref_id = ?",
            (*args, pref_id),
        )
        con.commit()
    if cur.rowcount == 0:
        raise HTTPException(404, "preference not found")
    return {"ok": True, "pref_id": pref_id}


@router.delete("/{pref_id}")
def delete_preference(pref_id: str):
    with sqlite3.connect(_db_path()) as con:
        cur = con.execute(
            "DELETE FROM user_style_preferences WHERE pref_id = ?",
            (pref_id,),
        )
        con.commit()
    if cur.rowcount == 0:
        raise HTTPException(404, "preference not found")
    return {"ok": True, "pref_id": pref_id}


@router.post("/learn")
async def trigger_learning(project_id: str = Query(...), force: bool = Query(default=False)):
    """Manually run the batch preference learner.

    ``force=true`` bypasses the every-N-chapters interval check by
    temporarily treating the interval as met (the analyzer still needs
    at least one chapter with an edit diff or special requirements).
    """
    from ui.backend.app.services.commit_pipeline.sub_tasks import (
        SubTaskContext, preference_analyzer,
    )
    ctx = SubTaskContext(
        project_id=project_id, chapter_id="", db_path=_db_path(),
    )
    if force:
        # Rewind the run marker so the interval gate passes.
        with sqlite3.connect(ctx.db_path) as con:
            try:
                con.execute(
                    "DELETE FROM preference_learning_runs WHERE project_id = ?",
                    (project_id,),
                )
                con.commit()
            except sqlite3.OperationalError:
                pass
    try:
        result = await preference_analyzer.run(ctx)
    except Exception as e:
        raise HTTPException(500, f"preference learning failed: {e}")
    return {"ok": True, **result}
