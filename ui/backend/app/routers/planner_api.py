"""FastAPI router: Outline, volume, and chapter planning."""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/api/planner", tags=["planner"])
logger = logging.getLogger("inkoctobot.ui.backend.planner_api")


class VolumePlanRequest(BaseModel):
    project_id: str
    overall_outline: str
    target_chapters_per_volume: int = 30
    world_book_summary: str = ""
    character_list: str = ""


class ChapterPlanRequest(BaseModel):
    project_id: str
    volume_outline: str
    chapter_num: int
    previous_summary: str = ""
    character_states: str = ""
    unresolved_threads: str = ""
    constraints: str = ""


@router.get("/health")
def health():
    return {"status": "ok", "router": "planner"}


@router.post("/volume")
async def plan_volumes(req: VolumePlanRequest):
    try:
        from agents.planner.volume_planner import VolumePlanner
        from models.router import ModelRouter
        router_inst = ModelRouter()
        planner = VolumePlanner(router=router_inst, project_id=req.project_id)
        result = await planner.plan_volumes(
            req.overall_outline,
            target_chapters_per_volume=req.target_chapters_per_volume,
            world_book_summary=req.world_book_summary,
            character_list=req.character_list,
        )
        return {"status": "ok", "volumes": result}
    except Exception as e:
        logger.error("Volume planning error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/chapter")
async def plan_chapter(req: ChapterPlanRequest):
    try:
        from agents.planner.chapter_planner import ChapterPlanner
        from models.router import ModelRouter
        router_inst = ModelRouter()
        planner = ChapterPlanner(router=router_inst, project_id=req.project_id)
        result = await planner.plan_chapter(
            req.volume_outline, req.chapter_num,
            previous_summary=req.previous_summary,
            character_states=req.character_states,
            unresolved_threads=req.unresolved_threads,
            constraints=req.constraints,
        )
        return {"status": "ok", "chapter_plan": result}
    except Exception as e:
        logger.error("Chapter planning error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))
