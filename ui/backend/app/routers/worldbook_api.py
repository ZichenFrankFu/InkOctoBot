"""
/api/worldbook — World book AI features.

Real CRUD is handled by data_api (/api/data/worldbook).
This provides AI-powered consistency checking.
"""
from __future__ import annotations

import json
import logging
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/api/worldbook", tags=["worldbook"])
logger = logging.getLogger("inkoctobot.ui.backend.worldbook_api")


class ConsistencyRequest(BaseModel):
    project_id: str = ""
    entries_text: str = ""
    entries: list[dict] = []
    provider: str = ""
    model: str = ""


def _build_router(provider: str = "", model: str = ""):
    from ui.backend.app.routers.generation_api import _build_router as build
    return build(provider, model)


@router.get("/status")
def worldbook_status():
    return {"status": "ok", "router": "worldbook"}


@router.post("/consistency-check")
async def consistency_check(req: ConsistencyRequest):
    """Check world book entries for internal contradictions using AI."""
    text = req.entries_text
    if not text and req.entries:
        text = "\n\n".join([
            f"[{e.get('title', '无标题')}] ({e.get('category', '')})\n{e.get('content', '')}"
            for e in req.entries
        ])
    if not text.strip():
        return {"status": "ok", "issues": [], "result": "没有条目需要检查"}

    try:
        from llm.base import LLMMessage
        router_inst = _build_router(req.provider, req.model)

        messages = [
            LLMMessage(role="system", content=(
                "你是一个世界观一致性检查专家。仔细检查以下世界书条目是否存在内部矛盾或逻辑冲突。"
                "列出所有发现的问题，每条一行。如果没有矛盾，请说明设定一致性良好。"
            )),
            LLMMessage(role="user", content=f"请检查以下世界书条目的一致性：\n\n{text}"),
        ]

        response = await router_inst.generate(
            agent_role="evaluator",
            messages=messages,
            temperature=0.3,
        )

        # Parse issues from response
        content = response.content.strip()
        issues = []
        for line in content.split("\n"):
            line = line.strip()
            if line and not line.startswith("没有") and not line.startswith("未发现") and not line.startswith("设定一致"):
                # Remove leading number/bullet
                cleaned = line.lstrip("0123456789.-）)、· ")
                if cleaned:
                    issues.append(cleaned)

        return {
            "status": "ok",
            "result": content,
            "issues": issues if issues else [],
        }
    except Exception as e:
        logger.error("Consistency check error: %s", e)
        raise HTTPException(500, str(e))
