"""/api/knowledge — 专业知识自学习 (spec 4.2).

POST /research is the user's confirmation act (机制1): it runs the
web-search compilation and stores the result directly as a
kind='knowledge' skill (机制2 — no second confirmation). The list /
update / delete surface lets the user 查看、修改所学知识.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])


def _db() -> str:
    from ui.backend.app.services.project_paths import get_db_path
    return get_db_path()


class ResearchRequest(BaseModel):
    project_id: str = ""
    domain: str
    context: str = ""


@router.post("/research")
async def research(req: ResearchRequest):
    from ui.backend.app.services.knowledge_research import research_domain
    try:
        result = await research_domain(
            _db(), req.project_id, req.domain, context=req.context,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(500, f"研究失败: {e}")
    return {"ok": True, **result}


@router.get("/skills")
def list_skills(project_id: str = Query(default="")):
    from ui.backend.app.services.knowledge_research import (
        list_knowledge_skills,
    )
    return {"items": list_knowledge_skills(_db())}


class KnowledgePatch(BaseModel):
    display_name: str | None = None
    body: str | None = None


@router.put("/skills/{skill_id}")
def update_skill(skill_id: str, patch: KnowledgePatch):
    from ui.backend.app.services.knowledge_research import (
        update_knowledge_skill,
    )
    try:
        out = update_knowledge_skill(
            _db(), skill_id,
            display_name=patch.display_name, body=patch.body,
        )
    except ValueError as e:
        raise HTTPException(404, str(e))
    return {"ok": True, **out}


@router.delete("/skills/{skill_id}")
def delete_skill(skill_id: str):
    from ui.backend.app.services.knowledge_research import (
        delete_knowledge_skill,
    )
    try:
        delete_knowledge_skill(_db(), skill_id)
    except ValueError as e:
        raise HTTPException(404, str(e))
    return {"ok": True}
