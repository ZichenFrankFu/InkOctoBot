"""
/api/characters — Character management.

Real character CRUD is handled by data_api (/api/data/characters).
This router provides character-specific AI features.
"""
from __future__ import annotations

import logging
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/api/characters", tags=["characters"])
logger = logging.getLogger("inkoctobot.ui.backend.characters_api")


class GenerateProfileRequest(BaseModel):
    name: str
    role: str = ""
    context: str = ""
    provider: str = "ollama"
    model: str = ""


@router.get("/status")
def characters_status():
    return {"status": "ok", "router": "characters"}


@router.post("/generate-profile")
async def generate_profile(req: GenerateProfileRequest):
    """Use AI to generate a character profile from a name and role."""
    try:
        from agents.model_providers.base import LLMMessage
        from agents.model_router import ModelRouter
        router_inst = ModelRouter()

        messages = [
            LLMMessage(role="system", content=(
                "你是一个专业的小说角色设计师。根据角色名字和定位，"
                "生成详细的角色档案。用 JSON 格式输出：\n"
                '{"personality": "...", "background": "...", "speech_style": "...", '
                '"appearance": "...", "tags": ["tag1", "tag2"]}'
            )),
            LLMMessage(role="user", content=f"角色名：{req.name}\n定位：{req.role}\n背景：{req.context}"),
        ]

        response = await router_inst.generate(
            agent_role="actor_default",
            messages=messages,
            temperature=0.8,
        )
        return {"status": "ok", "profile": response.content, "model": response.model}
    except Exception as e:
        logger.error("Generate profile error: %s", e)
        raise HTTPException(500, str(e))
