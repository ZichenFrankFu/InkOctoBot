"""/api/rollback — 事务性回溯 (spec 回溯·机制2/机制3).

Two-step flow honoring the confirm-before-destruction rule:
1. ``GET /api/rollback/preview`` returns the counts of everything a
   rollback would remove — the UI shows this and asks the user to
   confirm.
2. ``POST /api/rollback/execute`` performs the rollback inside one
   SQL transaction (all four state groups together, or nothing).
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from ui.backend.app.services.rollback import (
    preview_rollback, rollback_to_chapter,
)

router = APIRouter(prefix="/api/rollback", tags=["rollback"])


def _db_path() -> str:
    from ui.backend.app.services.project_paths import get_db_path
    return get_db_path()


@router.get("/preview")
def preview(
    project_id: str = Query(...),
    target_chapter: int = Query(..., ge=0),
):
    try:
        return preview_rollback(_db_path(), project_id, target_chapter)
    except Exception as e:
        raise HTTPException(500, f"rollback preview failed: {e}")


class RollbackRequest(BaseModel):
    project_id: str
    target_chapter: int
    # The UI must echo the preview back as explicit confirmation —
    # prevents accidental destructive calls from stale clients.
    confirm: bool = False


@router.post("/execute")
def execute(req: RollbackRequest):
    if not req.confirm:
        raise HTTPException(
            400, "rollback requires confirm=true (review the preview first)",
        )
    if req.target_chapter < 0:
        raise HTTPException(400, "target_chapter must be >= 0")
    try:
        result = rollback_to_chapter(
            _db_path(), req.project_id, req.target_chapter,
        )
    except Exception as e:
        raise HTTPException(500, f"rollback failed (nothing applied): {e}")
    return {"ok": True, **result}
