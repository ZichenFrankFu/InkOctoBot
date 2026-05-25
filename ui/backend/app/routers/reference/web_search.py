"""AI metadata completion via web search.

Two endpoints:
  - GET  /web_search/capability      — does the configured reference_web_search
                                        role's provider+model actually support
                                        web search? Used by the UI to gate
                                        the AI-complete button.
  - POST /works/{ref_id}/ai_complete — fill blank metadata fields (creator
                                        / genre / serial_status / summary)
                                        by querying the web through the
                                        web-search-capable model. User edits
                                        are NEVER overwritten.
"""
from __future__ import annotations

import json
import re
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ._common import MEDIA_TYPE_ZH, SERIAL_STATUS_VALUES, db

router = APIRouter()


@router.get("/web_search/capability")
def web_search_capability():
    """Return whether the configured ``reference_web_search`` role's
    provider+model is in the known web-search-capable set."""
    try:
        from llm.router import ModelRouter
        from llm.web_search_capabilities import supports_web_search, describe
        router_inst = ModelRouter()
        provider, model = router_inst.resolve_role("reference_web_search")
        enabled = supports_web_search(provider, model)
        return {
            "enabled": enabled,
            "provider": provider, "model": model,
            "reason": describe(provider, model),
        }
    except Exception as e:
        return {
            "enabled": False, "provider": "", "model": "",
            "reason": f"加载模型路由失败：{e}",
        }


def _strip_json_blob(raw: str) -> str:
    """Pull a JSON object out of a string that may contain markdown fences."""
    s = (raw or "").strip()
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", s, re.DOTALL)
    if fence:
        s = fence.group(1).strip()
    a = s.find("{")
    b = s.rfind("}")
    if 0 <= a < b:
        s = s[a:b + 1]
    return s


class AiCompleteRequest(BaseModel):
    prompt_override: Optional[str] = None  # per-call override


@router.post("/works/{ref_id}/ai_complete")
async def ai_complete_work(ref_id: str, body: AiCompleteRequest | None = None):
    """Use the configured ``reference_web_search`` model to fill metadata
    fields (creator / genre / serial_status / user_summary).

    Only fills fields the user hasn't already set; user edits are preserved.
    """
    rdb = db()
    w = rdb.get_work(ref_id)
    if not w:
        raise HTTPException(404, "参考作品不存在")

    try:
        from llm.router import ModelRouter
        from llm.web_search_capabilities import supports_web_search, describe
        router_inst = ModelRouter()
        provider, model = router_inst.resolve_role("reference_web_search")
    except Exception as e:
        raise HTTPException(500, f"模型路由初始化失败：{e}")

    if not supports_web_search(provider, model):
        raise HTTPException(400, describe(provider, model))

    author_hint = (
        f"已知作者：{w['creator']}（可用作辅助检索；如有更准确的全名请覆盖）"
        if w.get("creator") else "作者未知，请通过标题检索"
    )
    from reference_pipeline.prompts import render as _render_prompt
    prompt = _render_prompt(
        "reference.ai_complete",
        override=(body.prompt_override if body else None),
        media_type_zh=MEDIA_TYPE_ZH.get(w.get("media_type", ""), "作品"),
        title=w.get("title", ""),
        author_hint=author_hint,
    )

    try:
        raw = await router_inst.invoke_with_web_search(
            role="reference_web_search", prompt=prompt,
            max_tokens=1024, temperature=0.2,
        )
    except NotImplementedError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(502, f"联网调用失败：{e}")

    try:
        result = json.loads(_strip_json_blob(raw))
        if not isinstance(result, dict):
            raise ValueError("response is not a JSON object")
    except Exception as e:
        raise HTTPException(502, f"模型返回的 JSON 无法解析：{e}")

    # Only fill empty fields (preserve user edits)
    fields: dict = {}
    updated_keys: list[str] = []

    def _has(k: str) -> bool:
        v = w.get(k)
        return v not in (None, "", 0)

    new_creator = (result.get("creator") or "").strip()
    if new_creator and not _has("creator"):
        fields["creator"] = new_creator
        updated_keys.append("作者")

    new_genres = result.get("genres") or []
    if isinstance(new_genres, list) and not _has("genre"):
        parts = [str(g).strip() for g in new_genres if str(g).strip()]
        if parts:
            fields["genre"] = "，".join(parts[:5])
            updated_keys.append("题材")

    new_serial = (result.get("serial_status") or "").strip().lower()
    if new_serial in SERIAL_STATUS_VALUES and not _has("serial_status"):
        fields["serial_status"] = new_serial
        updated_keys.append("连载状态")

    new_summary = (result.get("summary") or "").strip()
    if new_summary and not _has("user_summary"):
        fields["user_summary"] = new_summary[:200]
        updated_keys.append("一句话梗概")

    if not fields:
        return {
            "work": w, "updated_keys": [],
            "message": "已有字段均不为空，未做修改（如需重新生成请先清空字段）。",
            "provider": provider, "model": model,
            "raw_response": result,
        }

    updated = rdb.update_work(ref_id, **fields)
    return {
        "work": updated, "updated_keys": updated_keys,
        "provider": provider, "model": model,
        "raw_response": result,
    }
