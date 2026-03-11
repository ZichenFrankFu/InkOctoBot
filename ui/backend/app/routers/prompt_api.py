"""FastAPI router: Interactive disambiguation interface."""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/api/prompt", tags=["prompt"])
logger = logging.getLogger("inkoctobot.ui.backend.prompt_api")


class DisambiguateRequest(BaseModel):
    text: str
    context: str = ""


class ResolveRequest(BaseModel):
    original_text: str
    user_choices: dict[str, str]


@router.get("/health")
def health():
    return {"status": "ok", "router": "prompt"}


@router.post("/disambiguate")
async def disambiguate(req: DisambiguateRequest):
    try:
        from agents.constraints.disambiguator import Disambiguator
        from models.router import ModelRouter
        router_inst = ModelRouter()
        disam = Disambiguator(router=router_inst)
        result = await disam.disambiguate(req.text, context=req.context)
        return {"status": "ok", "result": result}
    except Exception as e:
        logger.error("Disambiguation error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/resolve")
async def resolve(req: ResolveRequest):
    try:
        from agents.constraints.disambiguator import Disambiguator
        from models.router import ModelRouter
        router_inst = ModelRouter()
        disam = Disambiguator(router=router_inst)
        resolved = await disam.resolve(req.original_text, req.user_choices)
        return {"status": "ok", "resolved_text": resolved}
    except Exception as e:
        logger.error("Resolution error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))
