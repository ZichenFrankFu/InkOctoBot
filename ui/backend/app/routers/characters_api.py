"""
/api/characters — Character management.

Real character CRUD is handled by data_api (/api/data/characters).
This router provides character-specific AI features.
"""
from __future__ import annotations

import json
import logging
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/api/characters", tags=["characters"])
logger = logging.getLogger("inkoctobot.ui.backend.characters_api")


class GenerateProfileRequest(BaseModel):
    name: str
    role: str = ""
    project_id: str = ""
    existing_personality: str = ""
    context: str = ""
    provider: str = ""
    model: str = ""


def _build_router(provider: str = "", model: str = ""):
    from ui.backend.app.services import build_router as build
    return build(provider, model)


@router.get("/status")
def characters_status():
    return {"status": "ok", "router": "characters"}


@router.post("/generate-profile")
async def generate_profile(req: GenerateProfileRequest):
    """Use AI to generate a character profile from a name and role.
    System + user prompts both routed through the prompt registry —
    overridable from 设置 → 提示词."""
    try:
        from llm.base import LLMMessage
        from reference_pipeline.prompts import render as _render_prompt
        router_inst = _build_router(req.provider, req.model)

        existing_block = (
            f"\n已有人设信息：{req.existing_personality}"
            if req.existing_personality else ""
        )
        messages = [
            LLMMessage(role="system",
                       content=_render_prompt("assistant.character_profile")),
            LLMMessage(role="user", content=_render_prompt(
                "assistant.character_profile_user",
                name=req.name, role=req.role, existing_block=existing_block,
            )),
        ]

        response = await router_inst.generate(
            agent_role="actor_default",
            messages=messages,
            temperature=0.8,
        )

        # Try to parse JSON from response
        content = response.content.strip()
        # Remove possible markdown code fence
        if content.startswith("```"):
            content = content.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        try:
            profile = json.loads(content)
        except json.JSONDecodeError:
            profile = {"personality": content}

        return {"status": "ok", "profile": profile, "model": getattr(response, 'model', '')}
    except Exception as e:
        logger.error("Generate profile error: %s", e)
        raise HTTPException(500, str(e))
