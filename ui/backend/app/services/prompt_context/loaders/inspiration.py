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

from ..loader_protocol import LoaderPlan, make_plan
from ..utils import (
    clip, cosine, current_embedding_model_key, embed_sync, section,
)

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


_BLOCK = "inspiration"
_TITLE = "相关灵感（用户灵感库）"


def _build_body(
    pinned: list[dict], recommended: list[dict],
) -> str:
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
    return "\n".join(parts)


def _render_block(pinned: list[dict], recommended: list[dict]) -> str:
    body = _build_body(pinned, recommended)
    if not body:
        return ""
    from ..budget_allocator import LOADER_BUDGETS
    return section(_TITLE, clip(body, LOADER_BUDGETS[_BLOCK]["target"]))


def plan(
    project_id: str,
    chapter_outline: str = "",
    on_stage_characters: list[str] | None = None,
    *,
    user_pinned_ids: list[str] | None = None,
    chapter_num: int = 0,
    top_k: int = 3,
    min_relevance: float = 0.35,
    exclude: set | None = None,
) -> LoaderPlan | None:
    pinned, recommended = _select(
        project_id, chapter_outline, on_stage_characters or [],
        user_pinned_ids or [], chapter_num, top_k, min_relevance,
        exclude or set(),
    )
    return make_plan(_BLOCK, _TITLE, _build_body(pinned, recommended))


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
    p = plan(
        project_id, chapter_outline, on_stage_characters,
        user_pinned_ids=user_pinned_ids, chapter_num=chapter_num,
        top_k=top_k, min_relevance=min_relevance, exclude=exclude,
    )
    return p.render(p.target) if p else ""


def _select(
    project_id: str, chapter_outline: str, on_stage_characters: list[str],
    user_pinned_ids: list[str], chapter_num: int, top_k: int,
    min_relevance: float, exclude: set,
) -> tuple[list[dict], list[dict]]:
    """Run the query + ranking; return (pinned, recommended)."""
    try:
        db = _idea_db()
        rows = db.list_inspirations(
            project_id=project_id or None, include_embedding=True,
        )
        if not rows:
            return [], []

        rows = [r for r in rows if r["id"] not in exclude]
        if not rows:
            return [], []

        pin_set = set(user_pinned_ids)
        pinned = [r for r in rows if r["id"] in pin_set]
        candidates = [
            r for r in rows
            if r["id"] not in pin_set
               and not _used_too_often(r, chapter_num)
        ]

        on_stage = list(on_stage_characters)
        query = (chapter_outline or "").strip()
        if on_stage:
            query = f"{query}\n人物：{','.join(on_stage)}".strip()

        from knowledge.idea_db import inspiration_embedding_text, hash_embedding_text
        current_model_key = current_embedding_model_key()
        stale_idx: list[int] = []
        stale_texts: list[str] = []
        mismatched = 0
        for i, r in enumerate(rows):
            text = inspiration_embedding_text(r)
            current_hash = hash_embedding_text(text)
            existing = r.get("embedding") or []
            stored_model = r.get("embedding_model_key") or ""
            cross_model = (
                bool(existing) and bool(stored_model)
                and stored_model != current_model_key
            )
            if cross_model:
                mismatched += 1
                r["_cross_model"] = True
                continue
            if not existing or r.get("embedding_text_hash") != current_hash:
                stale_idx.append(i)
                stale_texts.append(text)
                r["_text_hash"] = current_hash
        if mismatched:
            logger.debug(
                "inspiration: %d entries embedded with a different model; "
                "skipped from ranking.", mismatched,
            )

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

            for j, idx in enumerate(stale_idx):
                vec = stale_vecs[j] if j < len(stale_vecs) else []
                if vec:
                    rows[idx]["embedding"] = vec
                    try:
                        db.set_embedding(rows[idx]["id"], vec,
                                          rows[idx]["_text_hash"],
                                          model_key=current_model_key)
                    except Exception as e:
                        logger.debug("persist embedding failed for %s: %s",
                                      rows[idx].get("id"), e)

            if not query_vec:
                recommended = [r for r in candidates if not r.get("_cross_model")][:remaining]
            else:
                scored: list[tuple[float, dict]] = []
                for r in candidates:
                    if r.get("_cross_model"):
                        continue
                    vec = r.get("embedding") or []
                    score = cosine(query_vec, vec) if vec else float("-inf")
                    scored.append((score, r))
                scored.sort(key=lambda t: t[0], reverse=True)
                for score, r in scored[:remaining]:
                    if score < min_relevance:
                        break
                    recommended.append(r)

        return pinned, recommended
    except Exception as e:
        logger.debug("inspiration skipped: %s", e)
        return [], []
