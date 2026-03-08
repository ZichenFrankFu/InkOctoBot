"""
/api/worldbook — World book AI features.

Real CRUD is handled by data_api (/api/data/worldbook).
This provides AI-powered consistency checking.
"""
from __future__ import annotations

import logging
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/api/worldbook", tags=["worldbook"])
logger = logging.getLogger("inkoctobot.ui.backend.worldbook_api")


class ConsistencyRequest(BaseModel):
    project_id: str
    entries: list[dict] = []
    provider: str = "ollama"
    model: str = ""


@router.get("/status")
def worldbook_status():
    return {"status": "ok", "router": "worldbook"}


@router.post("/consistency-check")
async def consistency_check(req: ConsistencyRequest):
    """Check world book entries for internal contradictions using AI."""
    if not req.entries:
        return {"status": "ok", "issues": [], "message": "没有条目需要检查"}

    try:
        from agents.model_providers.base import LLMMessage
        from agents.model_router import ModelRouter
        router_inst = ModelRouter()

        entries_text = "\n\n".join([
            f"【{e.get('title', '无标题')}】({e.get('category', '')})\n{e.get('content', '')}"
            for e in req.entries
        ])

        messages = [
            LLMMessage(role="system", content=(
                "你是一个世界观一致性检查专家。检查以下世界书条目是否存在内部矛盾。"
                "以 JSON 数组格式列出发现的问题：\n"
                '[{"entry1": "标题1", "entry2": "标题2", "contradiction": "矛盾描述", "severity": "high/medium/low"}]'
                "\n如果没有矛盾，返回空数组 []"
            )),
            LLMMessage(role="user", content=entries_text),
        ]

        response = await router_inst.generate(
            agent_role="evaluator",
            messages=messages,
            temperature=0.3,
        )
        return {"status": "ok", "result": response.content, "model": response.model}
    except Exception as e:
        logger.error("Consistency check error: %s", e)
        raise HTTPException(500, str(e))
