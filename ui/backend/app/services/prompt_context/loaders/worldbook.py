"""Worldbook (世界书) loader — outline-driven similarity injection."""
from __future__ import annotations

import logging
from typing import Any

from ..budget_allocator import LOADER_BUDGETS
from ..loader_protocol import LoaderPlan
from ..utils import clip, cosine, embed_sync, section

logger = logging.getLogger("inkoctobot.services.prompt_context.worldbook")


_BLOCK = "worldbook"
_TITLE = "世界观设定（世界书）"


def _format_entries(entries: list[dict], exclude: set | None) -> str:
    """Format a pre-ordered list of entries into the prompt body (no clip)."""
    lines: list[str] = []
    for e in entries:
        eid = str(e.get("id") or e.get("title") or "")
        if exclude and eid in exclude:
            continue
        title = (e.get("title") or "").strip()
        content = (e.get("content") or "").strip()
        if not title and not content:
            continue
        cat = (e.get("category") or "").strip()
        head = f"【{title}】" + (f"（{cat}）" if cat else "")
        lines.append(f"{head} {content}".strip())
    return "\n".join(lines)


def _render_entries(entries: list[dict], exclude: set | None) -> str:
    body = _format_entries(entries, exclude)
    if not body:
        return ""
    return section(_TITLE, clip(body, LOADER_BUDGETS[_BLOCK]["target"]))


def _prepare(project_id: str, chapter_id: str) -> list[dict] | None:
    """Run the query / embedding pipeline; return ordered rows or None."""
    try:
        from ui.backend.app.services import project_store
        from ui.backend.app.services.project_paths import get_db_path
        from .. import chapter_fields as _cf

        db_path = get_db_path()
        synopsis = ""
        if chapter_id:
            synopsis = (
                _cf.load_chapter_fields(project_id, chapter_id).get("synopsis") or ""
            ).strip()
        if not synopsis:
            return None

        rows = project_store.list_worldbook(db_path, project_id, include_embedding=True)
        if not rows:
            return None

        stale_idx: list[int] = []
        stale_texts: list[str] = []
        for i, e in enumerate(rows):
            text = project_store.worldbook_embedding_text(e)
            current_hash = project_store._hash_embedding_text(text)
            existing = e.get("embedding") or []
            if not existing or e.get("embedding_text_hash") != current_hash:
                stale_idx.append(i)
                stale_texts.append(text)
                e["_text_hash"] = current_hash

        batch_in = [synopsis] + stale_texts
        batch_out = embed_sync(batch_in)
        if not batch_out or not batch_out[0]:
            return rows  # embedding unavailable — unsorted fallback
        query_vec = batch_out[0]
        for j, idx in enumerate(stale_idx):
            new_vec = batch_out[1 + j] if 1 + j < len(batch_out) else []
            if not new_vec:
                continue
            rows[idx]["embedding"] = new_vec
            try:
                project_store.set_worldbook_embedding(
                    db_path, rows[idx]["id"], new_vec, rows[idx]["_text_hash"],
                )
            except Exception as ex:
                logger.debug("persist embedding failed for %s: %s",
                              rows[idx].get("id"), ex)

        scored: list[tuple[float, dict]] = []
        for e in rows:
            vec = e.get("embedding") or []
            score = cosine(query_vec, vec) if vec else float("-inf")
            scored.append((score, e))
        scored.sort(key=lambda t: t[0], reverse=True)
        return [e for _, e in scored]
    except Exception as e:
        logger.debug("worldbook skipped: %s", e)
        return None


def plan(
    project_id: str, chapter_id: str = "",
    exclude: set | None = None,
) -> LoaderPlan | None:
    ordered = _prepare(project_id, chapter_id)
    if not ordered:
        return None
    body = _format_entries(ordered, exclude)
    if not body:
        return None

    cfg = LOADER_BUDGETS[_BLOCK]
    overhead = len(_TITLE) + 6
    natural = overhead + len(body)

    def render(budget: int) -> str:
        return section(_TITLE, clip(body, max(0, budget - overhead)))

    return LoaderPlan(
        block_id=_BLOCK,
        natural_length=natural,
        minimum=cfg["min"], target=cfg["target"], maximum=cfg["max"],
        priority_tier=cfg["tier"], render=render,
    )


def load(project_id: str, chapter_id: str = "", exclude: set | None = None) -> str:
    p = plan(project_id, chapter_id, exclude)
    return p.render(p.target) if p else ""
