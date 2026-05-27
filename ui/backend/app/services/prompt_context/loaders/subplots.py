"""Subplots loader (LOADER_SPEC Loader 12).

Two-tier output:
- ``主线`` (thread_type='main'): every active main line, unconditional.
- ``相关支线`` (thread_type='sub'): top-K by cosine similarity against
  the chapter outline.

Inactive lines (status='resolved'/'dormant') are filtered everywhere —
they're done or paused; surfacing them would just confuse the Writer.

Embedding lifecycle mirrors the worldbook loader: per-row
``embedding_json`` + ``embedding_text_hash``. Missing or stale embeddings
get computed alongside the query in a single backend round-trip and
persisted on the fly. Backend failure → fall back to newest-first
unranked subs (main lines still render).
"""
from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
from typing import Any

from ..budgets import BUDGETS
from ..utils import clip, cosine, embed_sync, section

logger = logging.getLogger("inkoctobot.services.prompt_context.subplots")


_ACTIVE_STATUSES = ("setup", "building", "climax")


def _subplot_embedding_text(row: dict) -> str:
    """Canonical text for a subplot's embedding."""
    name = (row.get("name") or "").strip()
    desc = (row.get("description") or "").strip()
    return f"{name}\n{desc}" if (name or desc) else ""


def _hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _row_to_dict(row: sqlite3.Row) -> dict:
    out = dict(row)
    try:
        out["embedding"] = json.loads(out.get("embedding_json") or "[]")
    except (TypeError, json.JSONDecodeError):
        out["embedding"] = []
    return out


def _list_active_subplots(db_path: str, project_id: str) -> list[dict]:
    """All non-terminal subplots for the project."""
    placeholders = ",".join("?" for _ in _ACTIVE_STATUSES)
    sql = (
        "SELECT thread_id, project_id, name, description, status, "
        "thread_type, start_chapter, last_advanced_chapter, "
        "embedding_json, embedding_text_hash "
        "FROM subplot_threads "
        f"WHERE project_id = ? AND status IN ({placeholders}) "
        "ORDER BY thread_type DESC, start_chapter"  # 'sub' before 'main'? -> reverse
    )
    with sqlite3.connect(db_path) as con:
        con.row_factory = sqlite3.Row
        try:
            rows = con.execute(sql, (project_id, *_ACTIVE_STATUSES)).fetchall()
        except sqlite3.OperationalError:
            return []
    return [_row_to_dict(r) for r in rows]


def _persist_embedding(db_path: str, thread_id: str,
                       embedding: list[float], text_hash: str) -> None:
    try:
        with sqlite3.connect(db_path) as con:
            con.execute(
                "UPDATE subplot_threads SET embedding_json = ?, "
                "embedding_text_hash = ? WHERE thread_id = ?",
                (json.dumps(list(embedding), ensure_ascii=False),
                 text_hash, thread_id),
            )
            con.commit()
    except sqlite3.OperationalError as e:
        logger.debug("persist subplot embedding failed for %s: %s",
                      thread_id, e)


def _render(main_lines: list[dict], sub_lines: list[dict]) -> str:
    parts: list[str] = []
    if main_lines:
        parts.append("### 主线（必须推进或呼应）")
        for m in main_lines:
            line = f"- [{m['name']}]（{m['status']}）"
            if (m.get("description") or "").strip():
                line += (m.get("description") or "").strip()
            parts.append(line)
    if sub_lines:
        if parts:
            parts.append("")
        parts.append("### 相关支线（本章可推进）")
        for s in sub_lines:
            line = f"- [{s['name']}]（{s['status']}）"
            if (s.get("description") or "").strip():
                line += (s.get("description") or "").strip()
            parts.append(line)
    if not parts:
        return ""
    body = clip("\n".join(parts), BUDGETS["subplots"])
    return section("当前涉及的故事线", body)


def load(
    project_id: str,
    chapter_outline: str = "",
    chapter_num: int = 0,
    *,
    top_k: int = 5,
    min_relevance: float = 0.3,
    exclude: set | None = None,
) -> str:
    """Inject main lines + outline-similar sub lines."""
    try:
        from ui.backend.app.services.project_paths import get_db_path
        db_path = get_db_path()

        rows = _list_active_subplots(db_path, project_id)
        if not rows:
            return ""

        excl = exclude or set()
        rows = [r for r in rows if r["thread_id"] not in excl]
        if not rows:
            return ""

        main_lines = [r for r in rows if r.get("thread_type") == "main"]
        sub_pool = [r for r in rows if r.get("thread_type") != "main"]

        # Always-include main lines; rank sub lines by cosine similarity
        # to the chapter outline.
        selected_subs: list[dict] = []
        if sub_pool:
            # Collect stale-embedding texts + the query into one batch.
            stale_idx: list[int] = []
            stale_texts: list[str] = []
            for i, r in enumerate(sub_pool):
                text = _subplot_embedding_text(r)
                expected = _hash_text(text)
                if not r.get("embedding") or r.get("embedding_text_hash") != expected:
                    stale_idx.append(i)
                    stale_texts.append(text)
                    r["_text_hash"] = expected

            query = (chapter_outline or "").strip()
            batch_in = ([query] if query else []) + stale_texts
            batch_out = embed_sync(batch_in) if batch_in else []
            if query and batch_out:
                query_vec = batch_out[0] or []
                stale_vecs = batch_out[1:]
            else:
                query_vec = []
                stale_vecs = batch_out

            for j, idx in enumerate(stale_idx):
                vec = stale_vecs[j] if j < len(stale_vecs) else []
                if vec:
                    sub_pool[idx]["embedding"] = vec
                    _persist_embedding(
                        db_path, sub_pool[idx]["thread_id"], vec,
                        sub_pool[idx]["_text_hash"],
                    )

            if not query_vec:
                # Backend unavailable → newest-first by start_chapter desc.
                selected_subs = sorted(
                    sub_pool, key=lambda r: -(r.get("start_chapter") or 0),
                )[:top_k]
            else:
                scored: list[tuple[float, dict]] = []
                for r in sub_pool:
                    vec = r.get("embedding") or []
                    score = cosine(query_vec, vec) if vec else float("-inf")
                    scored.append((score, r))
                scored.sort(key=lambda t: t[0], reverse=True)
                for score, r in scored[:top_k]:
                    if score < min_relevance:
                        break
                    selected_subs.append(r)

        return _render(main_lines, selected_subs)
    except Exception as e:
        logger.debug("subplots loader skipped: %s", e)
        return ""
