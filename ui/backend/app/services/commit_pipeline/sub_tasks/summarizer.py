"""chapter_summarizer — LLM-generated reader-perspective summary.

Writes one row into ``chapter_summaries`` per commit:
- ``title``         : single-sentence headline
- ``summary_text``  : 100–300 字 body
- ``generated_by``  : ``'llm_auto'`` (manual edits later flip to ``'user_manual'``)
- ``llm_model``     : resolved provider/model for traceability
- ``generated_at``  : ISO timestamp

Strict reader-perspective: prompt forbids meta-vocabulary (伏笔 /
铺垫 / 作者 / etc) and information the reader shouldn't yet know.
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

logger = logging.getLogger("inkoctobot.commit_pipeline.summarizer")

if TYPE_CHECKING:
    from . import SubTaskContext


# Words that betray meta-narration — the writer should describe what
# happens in the chapter, not what it's "setting up".
_META_BANLIST = ("伏笔", "铺垫", "作者", "暗示", "为后", "为下")

_SYSTEM = (
    "你是小说编辑助手，写章节摘要给已读这一章的读者看。\n"
    "严格要求：\n"
    "1. 只用读者视角语言，禁止「伏笔」「铺垫」「作者」「暗示」「为后」「为下」等元词汇\n"
    "2. 不剧透读者此时尚不应知道的信息\n"
    "3. 包含本章核心事件 + 重要人物表现 + 章末状态\n"
    "4. 输出 JSON：{\"title\": \"一句话标题\", \"summary\": \"100-300字摘要\"}"
)

_USER_TMPL = (
    "章节号：第 {chapter_num} 章\n\n"
    "章节正文：\n"
    "{chapter_text}\n\n"
    "请输出 JSON。"
)


def _strip_meta(text: str) -> str:
    """Best-effort defensive strip — if the model leaked a banned word
    despite the prompt, blank the line containing it."""
    out: list[str] = []
    for line in text.splitlines():
        if any(b in line for b in _META_BANLIST):
            continue
        out.append(line)
    return "\n".join(out).strip()


def _extract_json(raw: str) -> dict:
    """Pull the first JSON object out of an LLM response. Tolerates
    code fences and prose around it."""
    s = raw.strip()
    if s.startswith("```"):
        # ```json ... ```
        s = re.sub(r"^```(?:json)?\s*", "", s)
        s = re.sub(r"\s*```$", "", s)
    # Greedy outermost {...}
    m = re.search(r"\{.*\}", s, flags=re.DOTALL)
    if not m:
        raise ValueError(f"no JSON object in LLM response: {raw[:200]}")
    return json.loads(m.group(0))


async def _call_llm(chapter_text: str, chapter_num: int) -> tuple[str, str, str]:
    """Returns (title, summary, model_label). Raises on any failure
    so the pipeline retry/notify path kicks in."""
    from llm.fallback_router import get_fallback_router
    from llm.router import ModelRouter
    router = ModelRouter()
    fr = get_fallback_router(router)
    raw = await fr.invoke(
        primary_role="post_commit",
        prompt=_USER_TMPL.format(
            chapter_num=chapter_num,
            chapter_text=chapter_text[:8000],   # cap context input
        ),
        system=_SYSTEM,
        max_tokens=600, temperature=0.3,
    )
    parsed = _extract_json(raw)
    title = str(parsed.get("title") or "").strip()[:120]
    summary = _strip_meta(str(parsed.get("summary") or "").strip())[:1200]
    if not title or not summary:
        raise ValueError(f"LLM response missing title/summary: {parsed}")
    provider, model = router.resolve_role("post_commit")
    return title, summary, f"{provider}/{model}".strip("/")


def _resolve_chapter_num(ctx: "SubTaskContext") -> int:
    """If the handler didn't pass chapter_num, infer from the chapter
    row. Returns 0 as final fallback (still writes the summary)."""
    if ctx.chapter_num:
        return ctx.chapter_num
    try:
        with sqlite3.connect(ctx.db_path) as con:
            row = con.execute(
                "SELECT chapter_num FROM chapters WHERE id = ?",
                (ctx.chapter_id,),
            ).fetchone()
            return int(row[0]) if row and row[0] else 0
    except Exception:
        return 0


def _resolve_chapter_text(ctx: "SubTaskContext") -> str:
    """Prefer handler-supplied text; if missing, pull latest active
    version from text_versions."""
    if ctx.chapter_text:
        return ctx.chapter_text
    try:
        with sqlite3.connect(ctx.db_path) as con:
            row = con.execute(
                "SELECT content FROM text_versions "
                "WHERE chapter_id = ? ORDER BY version_num DESC LIMIT 1",
                (ctx.chapter_id,),
            ).fetchone()
            return row[0] if row else ""
    except Exception:
        return ""


def _persist(
    db_path: str, project_id: str, chapter_num: int,
    title: str, summary: str, llm_model: str,
) -> str:
    """UPSERT — chapter_summaries has UNIQUE(project_id, chapter_num)
    so we either INSERT a fresh row or REPLACE the existing one.
    Returns the canonical summary_id (existing one on UPDATE)."""
    now = datetime.utcnow().isoformat(timespec="seconds")
    with sqlite3.connect(db_path) as con:
        existing = con.execute(
            "SELECT summary_id FROM chapter_summaries "
            "WHERE project_id = ? AND chapter_num = ?",
            (project_id, chapter_num),
        ).fetchone()
        if existing:
            sid = existing[0]
            con.execute(
                "UPDATE chapter_summaries SET "
                "summary_text = ?, title = ?, is_active = 1, "
                "generated_by = 'llm_auto', llm_model = ?, generated_at = ? "
                "WHERE summary_id = ?",
                (summary, title, llm_model, now, sid),
            )
        else:
            sid = f"sum_{uuid.uuid4().hex[:12]}"
            con.execute(
                "INSERT INTO chapter_summaries "
                "(summary_id, project_id, chapter_num, summary_text, title, "
                " is_active, generated_by, llm_model, generated_at) "
                "VALUES (?, ?, ?, ?, ?, 1, 'llm_auto', ?, ?)",
                (sid, project_id, chapter_num, summary, title,
                 llm_model, now),
            )
        con.commit()
    return sid


async def run(ctx: "SubTaskContext") -> dict[str, Any]:
    chapter_text = _resolve_chapter_text(ctx)
    if not chapter_text.strip():
        # Empty chapter (placeholder version, etc.) — nothing to summarize.
        return {"status": "skipped", "reason": "empty_chapter_text"}
    chapter_num = _resolve_chapter_num(ctx)
    title, summary, model_label = await _call_llm(chapter_text, chapter_num)
    sid = _persist(
        ctx.db_path, ctx.project_id, chapter_num,
        title, summary, model_label,
    )
    return {
        "status":      "ok",
        "summary_id":  sid,
        "title":       title,
        "chars":       len(summary),
        "llm_model":   model_label,
    }
