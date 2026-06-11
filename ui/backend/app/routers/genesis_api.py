"""/api/genesis — Storyland 创世 (spec 3.3.3.4).

Flow: run (one LLM call → proposal) → review (GET) → apply the
user-edited lists / discard. The proposal never writes canonical
tables until apply (机制6 审阅区).
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

router = APIRouter(prefix="/api/genesis", tags=["genesis"])


def _db_path() -> str:
    from ui.backend.app.services.project_paths import get_db_path
    return get_db_path()


@router.post("/run")
async def run(project_id: str = Query(...)):
    from ui.backend.app.services.genesis import run_genesis
    try:
        return await run_genesis(_db_path(), project_id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(500, f"genesis failed: {e}")


@router.get("/proposal")
def proposal(project_id: str = Query(...)):
    from ui.backend.app.services.genesis import get_pending_genesis
    p = get_pending_genesis(_db_path(), project_id)
    return p or {"proposal_id": None}


class ApplyRequest(BaseModel):
    project_id: str
    proposal_id: str
    # User-reviewed lists (edited / pruned in the review surface).
    # Omitted -> apply the stored proposal verbatim.
    entities: list[dict] | None = None
    facts: list[dict] | None = None


@router.post("/apply")
def apply(req: ApplyRequest):
    from ui.backend.app.services.genesis import apply_genesis
    try:
        result = apply_genesis(
            _db_path(), req.project_id, req.proposal_id,
            entities=req.entities, facts=req.facts,
        )
    except ValueError as e:
        raise HTTPException(409, str(e))
    except Exception as e:
        raise HTTPException(500, f"genesis apply failed (nothing written): {e}")
    return {"ok": True, **result}


@router.post("/discard")
def discard(project_id: str = Query(...), proposal_id: str = Query(...)):
    import sqlite3
    with sqlite3.connect(_db_path()) as con:
        cur = con.execute(
            "UPDATE pending_state_extractions SET status='discarded', "
            "resolved_at=CURRENT_TIMESTAMP "
            "WHERE proposal_id=? AND project_id=? AND status='pending'",
            (proposal_id, project_id),
        )
        con.commit()
    if cur.rowcount == 0:
        raise HTTPException(404, "no pending genesis proposal with that id")
    return {"ok": True, "proposal_id": proposal_id, "status": "discarded"}
