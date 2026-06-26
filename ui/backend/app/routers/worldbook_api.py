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
    from ui.backend.app.services import build_router as build
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
        from llm.call_site import with_audit_and_manual_mode
        from reference_pipeline.prompts import render as _render_prompt
        router_inst = _build_router(req.provider, req.model)

        # spec 世界书功能: 结果需要看到 条目A与条目B、其中冲突的内容、
        # 修改建议 — 要求结构化 JSON 输出。Prompts overridable via
        # 设置 → 提示词.
        system_prompt = _render_prompt("assistant.worldbook_consistency")
        user_prompt = _render_prompt(
            "assistant.worldbook_consistency_user", text=text,
        )

        async def _exec() -> str:
            messages = [
                LLMMessage(role="system", content=system_prompt),
                LLMMessage(role="user", content=user_prompt),
            ]
            response = await router_inst.generate(
                agent_role="evaluator",
                messages=messages,
                temperature=0.3,
            )
            return (response.content or "").strip()

        content = await with_audit_and_manual_mode(
            call_site_id="worldbook.consistency_check",
            primary_role="evaluator",
            prompt_full=user_prompt, system_prompt=system_prompt,
            auto_executor=_exec,
            parsed_target_table="worldbook_entries",
            project_id=req.project_id,
            provider_override=req.provider, model_override=req.model,
        )
        content = content.strip()

        # Structured conflicts (spec shape). Fallback: legacy line split
        # when the model didn't return parseable JSON.
        conflicts: list[dict] = []
        try:
            import json as _json
            import re as _re
            s = content
            if s.startswith("```"):
                s = _re.sub(r"^```(?:json)?\s*", "", s)
                s = _re.sub(r"\s*```$", "", s)
            m = _re.search(r"\{.*\}", s, flags=_re.DOTALL)
            if m:
                raw_conflicts = _json.loads(m.group(0)).get("conflicts") or []
                for c in raw_conflicts:
                    if not isinstance(c, dict):
                        continue
                    conflict_text = str(c.get("conflict") or "").strip()
                    if not conflict_text:
                        continue
                    conflicts.append({
                        "entry_a": str(c.get("entry_a") or "").strip(),
                        "entry_b": str(c.get("entry_b") or "").strip(),
                        "conflict": conflict_text,
                        "suggestion": str(c.get("suggestion") or "").strip(),
                    })
        except Exception:
            conflicts = []

        issues = [
            f"{c['entry_a']} × {c['entry_b']}：{c['conflict']}"
            for c in conflicts
        ]
        if not conflicts:
            # Legacy free-text fallback parse.
            for line in content.split("\n"):
                line = line.strip()
                if line and not line.startswith("没有") and not line.startswith("未发现") \
                        and not line.startswith("设定一致") and not line.startswith("{") \
                        and not line.startswith("}") and "conflicts" not in line:
                    cleaned = line.lstrip("0123456789.-）)、· ")
                    if cleaned and cleaned not in ("[", "]", "```", "```json"):
                        issues.append(cleaned)

        result_text = (
            "未发现明显矛盾，世界观设定一致性良好。" if not issues
            else f"发现 {len(issues)} 处潜在冲突，详见列表。"
        )
        return {
            "status": "ok",
            "result": result_text,
            "issues": issues,
            "conflicts": conflicts,
        }
    except Exception as e:
        logger.error("Consistency check error: %s", e)
        raise HTTPException(500, str(e))
