"""Domain knowledge acquisition — Part B compile step.

When the user accepts a domain suggestion (gate 1), this agent
compiles a knowledge document the user can review (gate 2) and,
once accepted, materialise as a knowledge skill.

Two modes:

- **API**: send a search-enabled LLM (``reference_web_search`` role)
  the brief. Must clear cost confirmation upstream — this module
  trusts the caller has done that. Output is markdown structured by
  the section headers the spec calls out (核心概念 / 常见误区 / 关键
  术语 / 写作可用细节 / 参考来源).
- **Manual**: return a research-instructions prompt for the user to
  paste into their browser LLM and bring the answer back. Reuses
  the same downstream "needs_review → accept" path as API mode.

The compile output is intentionally hedged: the system prompt asks
for "AI 综合改写不照抄" + "标(存疑)对不确定内容" + "列出参考来源"
because the spec's gate 2 is a soft review, not a truthiness audit.
"""
from __future__ import annotations

import logging
import sqlite3
import time
from typing import Any

logger = logging.getLogger(
    "inkoctobot.agents.learning.knowledge_acquisition"
)


_COMPILE_SYSTEM = (
    "你是为中文网络小说作者编译领域知识的助手。你会基于联网搜索的结果，"
    "把【{domain}】领域的关键信息整理成创作可用的资料。"
    "禁止照抄原文，必须综合改写；对不确定的内容明确标注\"(存疑)\"；"
    "末尾必须列出参考来源(标题/链接)。"
)


_COMPILE_PROMPT = (
    "请研究【{domain}】{sub_part}并整理为网络小说作者可直接使用的知识。"
    "理由：{rationale}\n\n"
    "输出 Markdown，必须含以下章节：\n"
    "## 核心概念\n"
    "## 常见误区 (作者最容易写错的点 — 重点)\n"
    "## 关键术语 (中英对照)\n"
    "## 写作可用细节 (能直接落进场景的具体素材)\n"
    "## 参考来源\n\n"
    "要求：综合改写不照抄；不确定内容标 \"(存疑)\"；"
    "适合中文网文行文密度，每节 200-500 字。"
)


_MANUAL_INSTRUCTION_HEADER = (
    "请把下面这段提示词粘贴到一个有联网搜索能力的对话型 AI "
    "（如 Claude.ai 网页版、ChatGPT、通义 / 百度 / DeepSeek 网页版），"
    "拿到回复后粘回本系统的【提交手动结果】框。\n\n"
    "─────── 提示词开始 ───────\n"
)


def _build_user_prompt(
    *, domain: str, sub_domain: str, rationale: str,
) -> str:
    sub_part = f"（重点：{sub_domain}）" if sub_domain else ""
    return _COMPILE_PROMPT.format(
        domain=domain, sub_part=sub_part, rationale=rationale or "—",
    )


def _build_system_prompt(domain: str) -> str:
    return _COMPILE_SYSTEM.format(domain=domain)


# ─────────── manual mode ───────────


def build_manual_research_prompt(
    *, domain: str, sub_domain: str = "", rationale: str = "",
) -> str:
    """Return the instruction text shown to the user for manual mode."""
    user = _build_user_prompt(
        domain=domain, sub_domain=sub_domain, rationale=rationale,
    )
    system = _build_system_prompt(domain)
    return (
        _MANUAL_INSTRUCTION_HEADER
        + system + "\n\n" + user
        + "\n─────── 提示词结束 ───────\n"
    )


# ─────────── API mode ───────────


class WebSearchUnavailable(RuntimeError):
    """Raised when no web-search-capable provider is configured.

    The caller (HTTP handler) catches this and surfaces a "降级手动模式"
    hint to the user instead of treating it as a hard failure."""


async def compile_via_web_search(
    *, domain: str, sub_domain: str = "", rationale: str = "",
    router_inst: Any = None,
) -> str:
    """Run the LLM compile step using a web-search-capable role.

    Returns the raw markdown. Raises ``WebSearchUnavailable`` when the
    configured provider can't search; raises other exceptions on
    transport failures (the handler should ``status='failed'``)."""
    if router_inst is None:
        from llm.router import ModelRouter
        router_inst = ModelRouter()

    user_prompt = _build_user_prompt(
        domain=domain, sub_domain=sub_domain, rationale=rationale,
    )
    system_prompt = _build_system_prompt(domain)
    full_prompt = f"{system_prompt}\n\n{user_prompt}"

    try:
        raw = await router_inst.invoke_with_web_search(
            role="reference_web_search",
            prompt=full_prompt,
            max_tokens=4096,
            temperature=0.3,
        )
    except NotImplementedError as e:
        raise WebSearchUnavailable(str(e)) from e
    return (raw or "").strip()


# ─────────── orchestration: gate-1 approve → compile → gate-2 needs_review ───────────


def _now() -> float:
    return time.time()


def _open(db_path: str) -> sqlite3.Connection:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    return con


def _load_suggestion(db_path: str, suggestion_id: str) -> dict | None:
    with _open(db_path) as con:
        row = con.execute(
            "SELECT * FROM domain_suggestions WHERE suggestion_id = ?",
            (suggestion_id,),
        ).fetchone()
    return dict(row) if row else None


def _update_status(
    db_path: str, suggestion_id: str,
    *, status: str, compiled_content: str | None = None,
    source_mode: str | None = None,
) -> None:
    sets = ["status = ?", "updated_at = ?"]
    params: list[Any] = [status, _now()]
    if compiled_content is not None:
        sets.append("compiled_content = ?")
        params.append(compiled_content)
    if source_mode is not None:
        sets.append("source_mode = ?")
        params.append(source_mode)
    params.append(suggestion_id)
    with _open(db_path) as con:
        con.execute(
            f"UPDATE domain_suggestions SET {', '.join(sets)} "
            "WHERE suggestion_id = ?",
            params,
        )
        con.commit()


async def run_compile_api(
    *, db_path: str, suggestion_id: str, router_inst: Any = None,
) -> dict[str, Any]:
    """Drive the API-mode compile end to end.

    Side effects:
      - moves the row to ``compiling`` while in flight
      - on success → ``needs_review`` with ``compiled_content`` populated
      - on web-search unavailability → ``failed`` (the API handler is
        expected to suggest manual mode at the same time)
      - on any other exception → ``failed``
    Returns ``{status, content?}``.
    """
    sug = _load_suggestion(db_path, suggestion_id)
    if not sug:
        return {"status": "missing"}

    _update_status(db_path, suggestion_id,
                   status="compiling", source_mode="api")
    try:
        content = await compile_via_web_search(
            domain=sug["domain"],
            sub_domain=sug.get("sub_domain") or "",
            rationale=sug.get("rationale") or "",
            router_inst=router_inst,
        )
    except WebSearchUnavailable as e:
        logger.info("web_search unavailable for %s: %s", suggestion_id, e)
        _update_status(db_path, suggestion_id, status="failed")
        return {"status": "failed", "reason": "web_search_unavailable",
                "hint": "manual"}
    except Exception as e:
        logger.error("compile_api failed for %s: %s",
                     suggestion_id, e, exc_info=True)
        _update_status(db_path, suggestion_id, status="failed")
        return {"status": "failed", "reason": str(e)[:200]}

    if not content.strip():
        _update_status(db_path, suggestion_id, status="failed")
        return {"status": "failed", "reason": "empty_response"}

    _update_status(db_path, suggestion_id,
                   status="needs_review", compiled_content=content)
    return {"status": "needs_review", "content": content}


def submit_manual_result(
    *, db_path: str, suggestion_id: str, pasted_content: str,
) -> dict[str, Any]:
    """Manual-mode counterpart to ``run_compile_api``: the user pastes
    their browser-LLM output here, we treat it the same as a successful
    API compile and move to ``needs_review``."""
    if not pasted_content.strip():
        return {"status": "rejected", "reason": "empty_paste"}
    sug = _load_suggestion(db_path, suggestion_id)
    if not sug:
        return {"status": "missing"}
    _update_status(
        db_path, suggestion_id,
        status="needs_review",
        compiled_content=pasted_content.strip(),
        source_mode="manual",
    )
    return {"status": "needs_review"}
