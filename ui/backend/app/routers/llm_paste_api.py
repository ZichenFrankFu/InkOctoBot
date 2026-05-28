"""Manual paste inbox API.

Endpoints:
    GET  /api/llm-paste/pending           — list every pending paste
    GET  /api/llm-paste/{token}           — full prompt + system + meta
    POST /api/llm-paste/{token}           — submit the response
    POST /api/llm-paste/{token}/cancel    — explicit cancel
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Body, HTTPException

from llm import manual_paste

logger = logging.getLogger("inkoctobot.routers.llm_paste_api")


router = APIRouter(prefix="/api/llm-paste", tags=["llm-paste"])


@router.get("/pending")
def list_pending() -> dict:
    return {"pending": manual_paste.list_pending()}


@router.get("/{token}")
def get_paste(token: str) -> dict:
    pp = manual_paste.get_pending(token)
    if pp is None:
        raise HTTPException(404, f"paste token {token!r} not found or expired")
    return {
        "paste_token":   pp.paste_token,
        "call_site_id":  pp.call_site_id,
        "project_id":    pp.project_id,
        "chapter_id":    pp.chapter_id,
        "prompt":        pp.prompt,
        "system":        pp.system,
        "created_at":    pp.created_at,
    }


@router.post("/{token}")
def submit_paste_endpoint(token: str, body: dict = Body(...)) -> dict:
    raw = body.get("response_raw")
    if raw is None or not isinstance(raw, str):
        raise HTTPException(400, "body.response_raw (string) required")
    ok = manual_paste.submit_paste(token, raw)
    if not ok:
        raise HTTPException(404, "paste already resolved or token unknown")
    return {"ok": True, "token": token, "response_chars": len(raw)}


@router.post("/{token}/cancel")
def cancel_paste_endpoint(token: str) -> dict:
    ok = manual_paste.cancel_paste(token)
    if not ok:
        raise HTTPException(404, "paste already resolved or token unknown")
    return {"ok": True, "token": token, "cancelled": True}
