"""多作品学习 (spec 参考数据库功能10) — /works/learn-common.

选多个参考作品 + 关注维度 → ONE LLM call 提取共通点 → 存为
kind='self_learned' skill（自学习成果，生成时按相关度召回注入）。
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ._common import reference_db_path

router = APIRouter(tags=["reference-learning"])


def _project_db() -> str:
    from ...services.project_paths import get_db_path
    return get_db_path()


class LearnCommonRequest(BaseModel):
    ref_ids: list[str]
    dimension: str = "overall"


@router.post("/works/learn-common")
async def learn_common(req: LearnCommonRequest):
    from ui.backend.app.services.common_pattern_learning import (
        learn_common_patterns,
    )
    try:
        result = await learn_common_patterns(
            _project_db(), reference_db_path(),
            req.ref_ids, req.dimension,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(500, f"共通点学习失败: {e}")
    return {"ok": True, **result}


@router.get("/learned-skills")
def list_self_learned():
    """自学习成果列表（多作品共通点 skill）。"""
    from ui.backend.app.services import skill_index
    items = [
        s for s in skill_index.list_skills(_project_db())
        if s.get("kind") == "self_learned"
    ]
    return {"items": items}


@router.delete("/learned-skills/{skill_id}")
def delete_self_learned(skill_id: str):
    from ui.backend.app.services import skill_index
    existing = skill_index.get_skill(_project_db(), skill_id)
    if existing is None or existing.get("kind") != "self_learned":
        raise HTTPException(404, "self-learned skill not found")
    skill_index.delete_skill(_project_db(), skill_id)
    return {"ok": True}
