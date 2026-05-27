"""Inspiration loader (LOADER_SPEC Loader 4).

Pulls the user's free-text idea snippets — those linked explicitly to
the chapter via ``user_pinned_ids`` plus a similarity-ranked top-K
selection grounded in the chapter outline + on-stage characters.

Inspirations live in their own SQLite file (``data/idea.db``) and are
optionally project-scoped: rows with ``project_id IS NULL`` are
"global" (visible to every project), rows with an explicit
``project_id`` are private to that project. The loader merges both
sets before ranking.

Embedding behavior mirrors the worldbook loader:
- Each row carries an ``embedding`` (JSON list) + ``embedding_text_hash``
- Missing/stale embeddings are batched with the query embedding into a
  single backend call and persisted on the fly
- Backend failure → fall back to all rows unranked (newest first) so
  the prompt still gets context, just not similarity-ordered.

Repeat-use guard: a row whose ``used_in_chapters`` contains the same
chapter ≥ 2 times is dropped from the auto-recommend set (but still
honored if user-pinned).
"""
from __future__ import annotations

import logging
from typing import Any

from ..budgets import BUDGETS
from ..utils import clip, cosine, embed_sync, section

logger = logging.getLogger("inkoctobot.services.prompt_context.inspiration")


def _idea_db_path() -> str:
    """Resolve the path to ``data/idea.db`` without depending on the
    routers layer. Falls back to the canonical default if the test/data
    dir override isn't available.
    """
    try:
        from ui.backend.app.routers.reference._common import idea_db_path
        return idea_db_path()
    except Exception:
        from ui.backend.app.services.project_paths import get_db_path
        from pathlib import Path
        return str(Path(get_db_path()).parent / "idea.db")


def _idea_db():
    from knowledge.idea_db import IdeaDB
    return IdeaDB(_idea_db_path())


def _used_too_often(row: dict, chapter_num: int) -> bool:
    """A row used ≥ 2 times in the same chapter is "over-mined"."""
    used = row.get("used_in_chapters") or []
    return sum(1 for c in used if int(c) == int(chapter_num)) >= 2


def _format_tags(row: dict) -> str:
    """Compact bracket prefix: prefer multi-tag, fall back to category."""
    tags = row.get("tags") or []
    if isinstance(tags, list) and tags:
        return f"[{' / '.join(str(t) for t in tags)}]"
    cat = (row.get("category") or "").strip()
    if cat and cat != "other":
        return f"[{cat}]"
    return ""


def _render_block(
    pinned: list[dict], recommended: list[dict],
) -> str:
    """Two-section output: explicit user picks, then system recs."""
    parts: list[str] = []
    if pinned:
        parts.append("### 用户显式关联")
        for r in pinned:
            tag = _format_tags(r)
            head = f"{tag} " if tag else ""
            parts.append(f"- {head}{(r.get('content') or '').strip()}")
    if recommended:
        if parts:
            parts.append("")
        parts.append("### 系统推荐（基于本章主题）")
        for r in recommended:
            tag = _format_tags(r)
            head = f"{tag} " if tag else ""
            parts.append(f"- {head}{(r.get('content') or '').strip()}")
    if not parts:
        return ""
    body = clip("\n".join(parts), BUDGETS["inspiration"])
    return section("相关灵感（用户灵感库）", body)


def load(
    project_id: str,
    chapter_outline: str = "",
    on_stage_characters: list[str] | None = None,
    *,
    user_pinned_ids: list[str] | None = None,
    chapter_num: int = 0,
    top_k: int = 3,
    min_relevance: float = 0.35,
    exclude: set | None = None,
) -> str:
    """Inject the inspirations most relevant to the chapter.

    User-pinned ids are always included (subject to ``exclude``). The
    remaining ``top_k - len(pinned)`` slots come from embedding
    similarity over the chapter outline + character names.
    """
    try:
        db = _idea_db()
        rows = db.list_inspirations(
            project_id=project_id or None, include_embedding=True,
        )
        if not rows:
            return ""

        excl: set[str] = set(exclude or [])
        rows = [r for r in rows if r["id"] not in excl]
        if not rows:
            return ""

        # Split pinned vs candidate pool.
        pin_set = set(user_pinned_ids or [])
        pinned = [r for r in rows if r["id"] in pin_set]
        # Auto-recommend candidates: not pinned + not over-mined.
        candidates = [
            r for r in rows
            if r["id"] not in pin_set
               and not _used_too_often(r, chapter_num)
        ]

        # Build the query string and the batch of stale-embedding texts
        # in one go so a single backend call services both.
        on_stage = list(on_stage_characters or [])
        query = (chapter_outline or "").strip()
        if on_stage:
            query = f"{query}\n人物：{','.join(on_stage)}".strip()

        from knowledge.idea_db import inspiration_embedding_text, hash_embedding_text
        stale_idx: list[int] = []
        stale_texts: list[str] = []
        for i, r in enumerate(rows):
            text = inspiration_embedding_text(r)
            current_hash = hash_embedding_text(text)
            existing = r.get("embedding") or []
            if not existing or r.get("embedding_text_hash") != current_hash:
                stale_idx.append(i)
                stale_texts.append(text)
                r["_text_hash"] = current_hash

        recommended: list[dict] = []
        remaining = max(0, top_k - len(pinned))

        if remaining > 0 and candidates:
            batch_in = ([query] if query else []) + stale_texts
            batch_out = embed_sync(batch_in) if batch_in else []
            query_vec: list[float] = []
            if query and batch_out:
                query_vec = batch_out[0] or []
                stale_vecs = batch_out[1:]
            else:
                stale_vecs = batch_out

            # Persist any newly-computed embeddings even if the query
            # itself failed — they'll save tokens next time.
            for j, idx in enumerate(stale_idx):
                vec = stale_vecs[j] if j < len(stale_vecs) else []
                if vec:
                    rows[idx]["embedding"] = vec
                    try:
                        db.set_embedding(rows[idx]["id"], vec,
                                          rows[idx]["_text_hash"])
                    except Exception as e:
                        logger.debug("persist embedding failed for %s: %s",
                                      rows[idx].get("id"), e)

            if not query_vec:
                # Embedding backend unavailable → fall back to
                # newest-first ordering (IdeaDB.list_inspirations sorts
                # by updated_at DESC already).
                recommended = candidates[:remaining]
            else:
                scored: list[tuple[float, dict]] = []
                for r in candidates:
                    vec = r.get("embedding") or []
                    score = cosine(query_vec, vec) if vec else float("-inf")
                    scored.append((score, r))
                scored.sort(key=lambda t: t[0], reverse=True)
                for score, r in scored[:remaining]:
                    if score < min_relevance:
                        break
                    recommended.append(r)

        return _render_block(pinned, recommended)
    except Exception as e:
        logger.debug("inspiration skipped: %s", e)
        return ""
