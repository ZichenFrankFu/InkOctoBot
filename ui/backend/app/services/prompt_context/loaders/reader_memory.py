"""Reader memory loader (LOADER_SPEC Loader 8).

Four-layer view of what the reader has been told as of the chapter
*before* the one being generated. Strictly causal — every query is
gated by ``chapter_num < K``. Layers:

- **L1 sliding window**: the ``window_size`` most-recent chapter
  summaries (so the new chapter can flow from immediate context).
- **L2 anchors**: user-marked "key chapter" summaries that should
  always be present, no matter how far back they happened.
- **L3 semantic recall**: free-text search through the project's
  ChromaDB vector store (``SemanticMemory``), filtered to
  ``chapter_num < K`` so we don't leak future spoilers.
- **L4 episodic timeline**: the most recent character-tagged events
  for the on-stage characters in this chapter (so the model knows
  what these specific people just did).

L3 and L4 silently degrade to empty when their backends are missing
(e.g. no ChromaDB installed, no episodic events seeded). L1 + L2
read from SQL only and always work.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from typing import Any

from ..budgets import BUDGETS
from ..utils import clip, section

logger = logging.getLogger("inkoctobot.services.prompt_context.reader_memory")


_DEFAULT_WINDOW = 8
_DEFAULT_SEMANTIC_TOP_K = 5
_DEFAULT_EPISODIC_LIMIT = 10
_DEFAULT_EPISODIC_LOOKBACK = 30


def _open(db_path: str) -> sqlite3.Connection:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    return con


def _query_recent_summaries(
    db_path: str, project_id: str, chapter_num: int, window: int,
) -> list[dict]:
    """L1: sliding window of recent chapter summaries (chapter_num < K)."""
    lo = max(0, chapter_num - window)
    with _open(db_path) as con:
        try:
            rows = con.execute(
                "SELECT chapter_num, title, summary_text "
                "FROM chapter_summaries "
                "WHERE project_id = ? AND chapter_num < ? "
                "AND chapter_num >= ? AND is_active = 1 "
                "ORDER BY chapter_num DESC",
                (project_id, chapter_num, lo),
            ).fetchall()
        except sqlite3.OperationalError:
            return []
    return [dict(r) for r in rows]


def _query_anchors(
    db_path: str, project_id: str, chapter_num: int,
) -> list[dict]:
    """L2: user-marked anchor chapters (still chapter_num < K)."""
    with _open(db_path) as con:
        try:
            rows = con.execute(
                "SELECT chapter_num, title, summary_text "
                "FROM chapter_summaries "
                "WHERE project_id = ? AND chapter_num < ? "
                "AND is_active = 1 AND is_anchor = 1 "
                "ORDER BY chapter_num",
                (project_id, chapter_num),
            ).fetchall()
        except sqlite3.OperationalError:
            return []
    return [dict(r) for r in rows]


def _query_semantic(
    project_id: str, chapter_num: int, query_text: str, top_k: int,
) -> list[dict]:
    """L3: ChromaDB semantic recall. Failure-tolerant — returns [] on
    any error so an unconfigured vector store doesn't block the loader.
    """
    if not query_text.strip():
        return []
    try:
        from knowledge.reader_memory.semantic_store import SemanticMemory
        sm = SemanticMemory()
        results = sm.query(
            query_text, project_id=project_id,
            n_results=max(top_k * 2, top_k + 2),
        )
    except Exception as e:
        logger.debug("semantic memory unavailable: %s", e)
        return []
    out: list[dict] = []
    for r in results:
        meta = r.get("metadata") or {}
        try:
            ch = int(meta.get("chapter_num") or 0)
        except (TypeError, ValueError):
            ch = 0
        if ch > 0 and ch >= chapter_num:
            continue  # spoiler guard
        out.append({
            "chapter_num": ch,
            "text":        r.get("document") or r.get("text") or "",
        })
        if len(out) >= top_k:
            break
    return out


def _query_episodic(
    db_path: str, project_id: str, chapter_num: int,
    on_stage_characters: list[str], *, lookback: int, limit: int,
) -> list[dict]:
    """L4: recent character-tagged events for the on-stage characters."""
    if not on_stage_characters:
        return []
    lo = max(0, chapter_num - lookback)
    with _open(db_path) as con:
        try:
            rows = con.execute(
                "SELECT chapter_num, event_type, description, "
                "characters_json FROM episodic_events "
                "WHERE project_id = ? AND chapter_num < ? AND chapter_num >= ? "
                "ORDER BY chapter_num DESC LIMIT ?",
                (project_id, chapter_num, lo, limit * 3),
            ).fetchall()
        except sqlite3.OperationalError:
            return []
    out: list[dict] = []
    on_stage_set = set(on_stage_characters)
    for r in rows:
        try:
            chars = json.loads(r["characters_json"] or "[]") or []
        except (TypeError, json.JSONDecodeError):
            chars = []
        if not on_stage_set.intersection(chars):
            continue
        out.append({
            "chapter_num": r["chapter_num"],
            "event_type":  r["event_type"],
            "description": r["description"],
        })
        if len(out) >= limit:
            break
    return out


def _render(
    chapter_num: int,
    recent: list[dict],
    anchors: list[dict],
    semantic: list[dict],
    episodic: list[dict],
) -> str:
    parts: list[str] = []
    if recent:
        parts.append("### 最近章节回顾（读者视角）")
        for r in recent:
            title = (r.get("title") or "").strip()
            head = f"**第 {r['chapter_num']} 章" + (f" {title}" if title else "") + "**"
            summary = (r.get("summary_text") or "").strip() or "（无摘要）"
            parts.append(f"{head}：{summary}")
    if anchors:
        if parts:
            parts.append("")
        parts.append("### 关键章节锚点")
        for a in anchors:
            title = (a.get("title") or "").strip()
            head = f"**第 {a['chapter_num']} 章" + (f" {title}" if title else "") + "**"
            summary = (a.get("summary_text") or "").strip() or "（无摘要）"
            parts.append(f"{head}：{summary}")
    if semantic:
        if parts:
            parts.append("")
        parts.append("### 语义相关历史片段")
        for s in semantic:
            ch = s.get("chapter_num") or 0
            head = f"（来自第 {ch} 章）" if ch else "（来自历史片段）"
            text = (s.get("text") or "").strip()
            if text:
                parts.append(f"{head}{text}")
    if episodic:
        if parts:
            parts.append("")
        parts.append("### 相关事件时间线")
        for e in episodic:
            parts.append(f"- 第 {e['chapter_num']} 章：{e['description']}")
    if not parts:
        return ""
    title = f"读者视角记忆（截至第 {chapter_num - 1} 章）"
    body = clip("\n".join(parts), BUDGETS["reader_memory"])
    return section(title, body)


def load(
    project_id: str,
    chapter_num: int,
    *,
    chapter_outline: str = "",
    on_stage_characters: list[str] | None = None,
    window_size: int = _DEFAULT_WINDOW,
    semantic_top_k: int = _DEFAULT_SEMANTIC_TOP_K,
    exclude: set | None = None,
) -> str:
    """Build the reader-memory block."""
    if chapter_num <= 1:
        return ""  # nothing has happened yet
    excl = exclude or set()
    try:
        from ui.backend.app.services.project_paths import get_db_path
        db_path = get_db_path()

        recent = (
            [] if "recent" in excl
            else _query_recent_summaries(
                db_path, project_id, chapter_num, window_size,
            )
        )
        anchors = (
            [] if "anchors" in excl
            else _query_anchors(db_path, project_id, chapter_num)
        )
        # De-dup: any anchor that's also in the recent window appears
        # once in the recent section (keeps the layered view tidy).
        recent_chs = {r["chapter_num"] for r in recent}
        anchors = [a for a in anchors if a["chapter_num"] not in recent_chs]

        semantic = (
            [] if "semantic" in excl
            else _query_semantic(
                project_id, chapter_num,
                (chapter_outline or "").strip(),
                semantic_top_k,
            )
        )
        episodic = (
            [] if "episodic" in excl
            else _query_episodic(
                db_path, project_id, chapter_num,
                on_stage_characters or [],
                lookback=_DEFAULT_EPISODIC_LOOKBACK,
                limit=_DEFAULT_EPISODIC_LIMIT,
            )
        )

        return _render(chapter_num, recent, anchors, semantic, episodic)
    except Exception as e:
        logger.debug("reader_memory skipped: %s", e)
        return ""
