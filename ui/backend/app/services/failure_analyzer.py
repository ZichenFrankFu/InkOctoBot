"""FailureAnalyzer — archive rejected generations + extract anti-hints.

LOADER_SPEC Loader 9 prerequisite. When the user discards a generated
chapter draft, we archive the content so the next ``fresh`` regeneration
can be told "avoid this direction". An LLM pass extracts up to 3
specific "issues" (one-line antihints) from the archived content.

The archive write is synchronous and fast. The LLM analyze step is async
and tolerant — on any failure ``issues_detected`` simply stays empty
and the loader degrades to "no anti-hints" gracefully.
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any

from .project_store import open_db, _row_to_dict

logger = logging.getLogger("inkoctobot.services.failure_analyzer")


_SNIPPET_CHARS = 300
_PROMPT_CONTENT_LIMIT = 3000
_MAX_ISSUES = 3


def _fid() -> str:
    return f"fg_{int(time.time() * 1000)}_{uuid.uuid4().hex[:6]}"


def archive_failed_generation(
    db_path: str, chapter_id: str, project_id: str,
    content: str, *, rejection_reason: str = "",
) -> str:
    """Archive a discarded draft. Returns the new failed_gen_id."""
    fid = _fid()
    snippet = (content or "")[:_SNIPPET_CHARS]
    with open_db(db_path) as con:
        con.execute(
            """INSERT INTO chapter_failed_generations (
                   failed_gen_id, chapter_id, project_id,
                   content_snippet, full_content,
                   rejected_at, rejection_reason, issues_detected)
               VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP, ?, '[]')""",
            (fid, chapter_id, project_id, snippet,
             content or "", rejection_reason or ""),
        )
        con.commit()
    return fid


def get_recent_failed_generations(
    db_path: str, chapter_id: str, limit: int = 2,
) -> list[dict[str, Any]]:
    """Return the most recent ``limit`` failed drafts, newest first."""
    with open_db(db_path) as con:
        rows = con.execute(
            "SELECT * FROM chapter_failed_generations WHERE chapter_id = ? "
            "ORDER BY rejected_at DESC LIMIT ?",
            (chapter_id, int(limit)),
        ).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        d = _row_to_dict(r)
        try:
            d["issues_detected"] = json.loads(d.get("issues_detected") or "[]")
        except (TypeError, json.JSONDecodeError):
            d["issues_detected"] = []
        out.append(d)
    return out


def get_failed_generation(
    db_path: str, failed_gen_id: str,
) -> dict[str, Any] | None:
    with open_db(db_path) as con:
        row = con.execute(
            "SELECT * FROM chapter_failed_generations WHERE failed_gen_id = ?",
            (failed_gen_id,),
        ).fetchone()
    if row is None:
        return None
    d = _row_to_dict(row)
    try:
        d["issues_detected"] = json.loads(d.get("issues_detected") or "[]")
    except (TypeError, json.JSONDecodeError):
        d["issues_detected"] = []
    return d


def _build_analyze_prompt(failed_content: str) -> str:
    return (
        "user 不满意以下章节内容并要求重新生成。\n"
        "分析其中可能让 user 不满意的具体方向（最多 3 条），用于下次生成时避免。\n\n"
        f"章节内容（截取）：\n{failed_content[:_PROMPT_CONTENT_LIMIT]}\n\n"
        "输出 JSON：{\"issues\": [\"...\", \"...\", \"...\"]}\n"
        "每条都要具体，比如:\n"
        '  "开场用了内心独白，节奏拖沓"\n'
        '  "对话太露骨，反派动机说得太白"\n'
        '  "配角戏份过多分散主线"\n'
    )


def _parse_issues_response(text: str) -> list[str]:
    """Extract ``{"issues": [...]}`` from an LLM response. Tolerates
    triple-fenced code blocks and surrounding prose.
    """
    if not text:
        return []
    snippet = text.strip()
    if "```" in snippet:
        # Pull the first ```json``` block, falling back to the first ```...```
        parts = snippet.split("```")
        for part in parts:
            cleaned = part.strip()
            if cleaned.startswith("json"):
                cleaned = cleaned[len("json"):].strip()
            if cleaned.startswith("{"):
                snippet = cleaned
                break
    # Find the JSON object spanning the first { ... last }
    if "{" in snippet and "}" in snippet:
        snippet = snippet[snippet.index("{"): snippet.rindex("}") + 1]
    try:
        parsed = json.loads(snippet)
    except json.JSONDecodeError:
        return []
    issues = parsed.get("issues") if isinstance(parsed, dict) else None
    if not isinstance(issues, list):
        return []
    out: list[str] = []
    for it in issues[:_MAX_ISSUES]:
        s = str(it or "").strip()
        if s:
            out.append(s)
    return out


async def analyze_failure(
    router: Any, db_path: str, failed_gen_id: str,
) -> list[str]:
    """LLM-extract anti-hint issues for an archived draft and persist them.

    ``router`` must expose ``await router.generate(messages=...)`` returning
    an object with ``.content``. On any failure (LLM error, parse error,
    empty list) we persist an empty list — the loader treats it as
    "no anti-hints" and moves on.
    """
    failed = get_failed_generation(db_path, failed_gen_id)
    if failed is None:
        logger.debug("analyze_failure: id %s not found", failed_gen_id)
        return []
    if failed.get("issues_detected"):
        # Already analyzed — return what we have, don't re-burn tokens.
        return failed["issues_detected"]
    try:
        prompt = _build_analyze_prompt(failed.get("full_content") or "")
        resp = await router.generate(messages=[
            {"role": "user", "content": prompt},
        ])
        text = getattr(resp, "content", "") or ""
        issues = _parse_issues_response(text)
    except Exception as e:
        logger.debug("analyze_failure llm/parse failed for %s: %s",
                     failed_gen_id, e)
        issues = []

    with open_db(db_path) as con:
        con.execute(
            "UPDATE chapter_failed_generations SET issues_detected = ? "
            "WHERE failed_gen_id = ?",
            (json.dumps(issues, ensure_ascii=False), failed_gen_id),
        )
        con.commit()
    return issues
