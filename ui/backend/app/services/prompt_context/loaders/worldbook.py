"""Worldbook (世界书) loader — outline-driven similarity injection.

Picks the entries most relevant to the chapter's outline rather than
dumping the whole worldbook. Flow:

1. Read the chapter's ``synopsis`` (大纲) from the editor doc.
2. If absent → return ``""`` (no outline, no targeted selection).
3. Compute the synopsis embedding via the configured embedding backend.
4. For each worldbook entry, ensure its stored embedding is fresh
   (recompute + persist if missing or stale via text-hash mismatch).
5. Rank by cosine similarity, apply the exclude set, take entries until
   the character budget is exhausted.

The embedding backend may be offline (local sentence-transformers not
installed, no OpenAI key, etc.) — in that case the loader falls back to
returning ALL entries (existing behavior) so generation still grounds
against the worldbook.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import logging
from typing import Any

from ..budgets import BUDGETS
from ..utils import clip, section

logger = logging.getLogger("inkoctobot.services.prompt_context.worldbook")


def _cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity for two equal-length vectors. 0.0 on bad input."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / ((na ** 0.5) * (nb ** 0.5))


def _embed_sync(texts: list[str]) -> list[list[float]]:
    """Synchronously embed a batch via the configured backend.

    Bridges from sync loader code into the async embedding API. Returns
    ``[[] for _ in texts]`` on any failure so callers can degrade
    gracefully.
    """
    if not texts:
        return []

    async def _do() -> list[list[float]]:
        from llm.embedding_provider import get_embedding_provider
        prov = get_embedding_provider()
        arr = await prov.embed(texts)
        try:
            return arr.tolist()  # numpy ndarray
        except AttributeError:
            return [list(v) for v in arr]

    try:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is not None:
            # Inside an async caller (FastAPI route) — run in a worker
            # thread so we don't block the loop or nest asyncio.run().
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                return pool.submit(asyncio.run, _do()).result()
        return asyncio.run(_do())
    except Exception as e:
        logger.debug("embedding failed: %s", e)
        return [[] for _ in texts]


def _render_entries(entries: list[dict], exclude: set | None) -> str:
    """Render an ordered list of worldbook entries into the prompt block."""
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
    if not lines:
        return ""
    return section("世界观设定（世界书）", clip("\n".join(lines), BUDGETS["worldbook"]))


def load(project_id: str, chapter_id: str = "", exclude: set | None = None) -> str:
    """Inject the worldbook entries most relevant to the chapter outline.

    No outline → ``""`` (per user-configured behavior). Embedding-backend
    failure → fall back to all entries (existing pre-similarity behavior).
    """
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
            return ""

        rows = project_store.list_worldbook(db_path, project_id, include_embedding=True)
        if not rows:
            return ""

        # Identify entries needing (re)embedding. Stale = hash mismatch
        # OR empty embedding. Compute query embedding in the same batch
        # so a single network round-trip serves both.
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
                e["_canonical"] = text

        # Batch: [query, *stale_texts] in one embed() call.
        batch_in = [synopsis] + stale_texts
        batch_out = _embed_sync(batch_in)
        if not batch_out or not batch_out[0]:
            # Embedding unavailable — fall back to all entries unranked.
            return _render_entries(rows, exclude)
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
            except Exception as e:
                logger.debug("persist embedding failed for %s: %s",
                              rows[idx].get("id"), e)

        # Score and sort by descending similarity. Entries without a
        # vector (embedding still failed) get -inf so they sink to the
        # bottom but remain available as filler when budget allows.
        scored: list[tuple[float, dict]] = []
        for e in rows:
            vec = e.get("embedding") or []
            score = _cosine(query_vec, vec) if vec else float("-inf")
            scored.append((score, e))
        scored.sort(key=lambda t: t[0], reverse=True)
        ordered = [e for _, e in scored]
        return _render_entries(ordered, exclude)
    except Exception as e:
        logger.debug("worldbook skipped: %s", e)
        return ""
