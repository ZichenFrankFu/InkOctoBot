"""
Reference-works hierarchical vector index.

Three levels populate the same ChromaDB collection (one collection per
embedding backend to avoid mixing dimensions):

- **L1** — chronicle events / characters / settings (AI-condensed,
  cheap, always indexed).
- **L2** — per-chapter summary: title + first 200 + last 200 chars.
- **L3** — raw 1500-char chunks (sentence-aware, 200-char overlap).
  Opt-in; expensive on multi-million-token works.

The indexer is **idempotent** (stable doc IDs, content-hash metadata
for skip-if-unchanged) and **resumable** (per-(ref, level) progress
persisted to ``work_index_progress`` so an interrupted L3 run continues
from the last persisted chunk).
"""
from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import time
from pathlib import Path
from typing import Any, Iterator

logger = logging.getLogger("inkoctobot.rag.work_index")


def _short_hash(text: str, n: int = 12) -> str:
    return hashlib.sha1(text.encode("utf-8", errors="replace")).hexdigest()[:n]


def _slug(s: str, maxlen: int = 24) -> str:
    """Make a stable filename-safe slug from a Chinese/English name."""
    keep = []
    for ch in s:
        if ch.isalnum() or "一" <= ch <= "鿿":
            keep.append(ch)
        elif ch in "_-":
            keep.append(ch)
    out = "".join(keep) or _short_hash(s, 8)
    return out[:maxlen]


# Vector search always returns the requested k rows, even when only a
# couple are genuinely related — the irrelevant long tail then dilutes
# the result list. Keep rows whose cosine distance is within a margin of
# the best match (the top hit is always kept) so the results the user
# sees are the ones actually close to the query.
_RELEVANCE_MARGIN = 0.32


def _filter_by_relevance(hits: list[dict], k: int) -> list[dict]:
    if not hits:
        return []
    ranked = sorted(hits, key=lambda h: h.get("distance", 0.0))
    best = ranked[0].get("distance", 0.0)
    kept = [h for h in ranked if h.get("distance", 0.0) <= best + _RELEVANCE_MARGIN]
    return kept[:k]


class WorkIndexer:
    """Build + maintain L1/L2/L3 vectors for a reference work."""

    def __init__(self, db_path: str, vector_store: Any, embedding_provider: Any,
                  batch_size: int = 32):
        self.db_path = db_path
        self._vs = vector_store
        self._emb = embedding_provider
        self.batch_size = batch_size

    # ── progress table helpers ──

    def _open(self) -> sqlite3.Connection:
        c = sqlite3.connect(self.db_path)
        c.execute("PRAGMA journal_mode=WAL")
        c.row_factory = sqlite3.Row
        return c

    def _set_progress(self, ref_id: str, level: str, *,
                       done: int | None = None, total: int | None = None,
                       last_ordinal: int | None = None, status: str | None = None,
                       error: str | None = None) -> None:
        with self._open() as c:
            row = c.execute(
                "SELECT done, total, last_ordinal, status FROM work_index_progress "
                "WHERE ref_id=? AND level=? AND backend=?",
                (ref_id, level, self._emb.name),
            ).fetchone()
            if row is None:
                c.execute(
                    "INSERT INTO work_index_progress "
                    "(ref_id, level, backend, done, total, last_ordinal, status, error, started_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
                    (ref_id, level, self._emb.name,
                     done or 0, total or 0,
                     last_ordinal if last_ordinal is not None else -1,
                     status or "running", error),
                )
            else:
                fields, params = [], []
                if done is not None: fields.append("done=?"); params.append(done)
                if total is not None: fields.append("total=?"); params.append(total)
                if last_ordinal is not None:
                    fields.append("last_ordinal=?"); params.append(last_ordinal)
                if status is not None: fields.append("status=?"); params.append(status)
                if error is not None: fields.append("error=?"); params.append(error)
                fields.append("updated_at=CURRENT_TIMESTAMP")
                params += [ref_id, level, self._emb.name]
                c.execute(
                    f"UPDATE work_index_progress SET {', '.join(fields)} "
                    f"WHERE ref_id=? AND level=? AND backend=?",
                    params,
                )
            c.commit()

    def get_progress(self, ref_id: str) -> list[dict]:
        with self._open() as c:
            rows = c.execute(
                "SELECT * FROM work_index_progress WHERE ref_id=?",
                (ref_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    # ── public methods ──

    async def index_l1(self, ref_id: str) -> dict:
        """L1: chronicle events + characters + settings (cheap, always run).
        Reads the work's currently-stored analysis JSON and (re-)embeds it."""
        from rag.reference_db import ReferenceDB
        rdb = ReferenceDB(self.db_path)
        work = rdb.get_work(ref_id)
        if not work:
            raise ValueError(f"work not found: {ref_id}")

        plot = self._safe_json(work.get("plot_outline_json"))
        chars = self._safe_json(work.get("extracted_characters_json"))
        settings_items = self._safe_json(work.get("settings_json"))
        title = work.get("title") or ref_id

        items: list[tuple[str, str, dict]] = []  # (doc_id, text, metadata)

        # Chronicle events
        for ei, ep in enumerate(((plot or {}).get("epochs") or [])):
            for pi, per in enumerate(ep.get("periods") or []):
                for evi, ev in enumerate(per.get("events") or []):
                    if not isinstance(ev, dict):
                        continue
                    name = ev.get("name") or ev.get("category") or ""
                    desc = ev.get("description") or ""
                    if not name and not desc:
                        continue
                    text = f"【{ev.get('subject','')}·{ev.get('category','')}·{name}】{desc}".strip()
                    doc_id = f"{ref_id}:L1:ev:{ei}:{pi}:{evi}"
                    items.append((doc_id, text, {
                        "ref_id": ref_id, "title": title,
                        "level": "L1", "source_type": "chronicle_event",
                        "time_marker": (ev.get("time_marker") or per.get("time") or ""),
                        "subject": ev.get("subject", ""),
                        "category": ev.get("category", ""),
                        "name": name,
                    }))

        # Characters
        if isinstance(chars, list):
            for c in chars:
                if not isinstance(c, dict):
                    continue
                name = (c.get("name") or "").strip()
                intro = (c.get("intro") or "").strip()
                if not name:
                    continue
                text = f"{name}：{intro}" if intro else name
                doc_id = f"{ref_id}:L1:ch:{_slug(name)}"
                items.append((doc_id, text, {
                    "ref_id": ref_id, "title": title,
                    "level": "L1", "source_type": "character",
                    "name": name,
                    "first_seen_at": (c.get("first_seen_at") or ""),
                    "mentions": int(c.get("mentions") or 0),
                }))

        # Settings
        if isinstance(settings_items, list):
            for s in settings_items:
                if not isinstance(s, dict):
                    continue
                t = (s.get("title") or "").strip()
                content = (s.get("content") or "").strip()
                if not t:
                    continue
                text = f"{t}：{content}" if content else t
                doc_id = f"{ref_id}:L1:st:{_slug(t)}"
                items.append((doc_id, text, {
                    "ref_id": ref_id, "title": title,
                    "level": "L1", "source_type": "setting",
                    "category": s.get("category", "other"),
                    "name": t,
                    "first_introduced_at": (s.get("first_introduced_at") or ""),
                }))

        if not items:
            self._set_progress(ref_id, "L1", done=0, total=0, status="done")
            return {"level": "L1", "embedded": 0}

        self._set_progress(ref_id, "L1", total=len(items), done=0, status="running")
        embedded = await self._embed_and_upsert(items)
        self._set_progress(ref_id, "L1", done=embedded, status="done")
        return {"level": "L1", "embedded": embedded}

    async def index_l2(self, ref_id: str) -> dict:
        """L2: per-chapter summary (title + head/tail snippets)."""
        from rag.reference_db import ReferenceDB
        rdb = ReferenceDB(self.db_path)
        work = rdb.get_work(ref_id)
        if not work:
            raise ValueError(f"work not found: {ref_id}")
        if not work.get("has_full_text") or not work.get("file_path"):
            self._set_progress(ref_id, "L2", done=0, total=0, status="done")
            return {"level": "L2", "embedded": 0, "skipped": "no full text"}

        from analysis.feature_extraction.pipeline import FeatureExtractionPipeline
        pipe = FeatureExtractionPipeline(self.db_path)
        text = pipe._load_text(work)
        if not text:
            self._set_progress(ref_id, "L2", done=0, total=0, status="done")
            return {"level": "L2", "embedded": 0, "skipped": "empty text"}
        chapters = pipe._split_chapters(text)

        # Pull chapter types from rhythm_json when available
        rhythm = self._safe_json(work.get("rhythm_json")) or {}
        types_by_chapter: dict[int, list[str]] = {}
        for cf in (rhythm.get("chapter_features") or []):
            ch = cf.get("chapter")
            if isinstance(ch, int):
                types_by_chapter[ch] = list(cf.get("types") or [])

        title = work.get("title") or ref_id
        items: list[tuple[str, str, dict]] = []
        for i, ch in enumerate(chapters, start=1):
            content = ch.get("content") or ""
            head = content[:200]
            tail = content[-200:] if len(content) > 400 else ""
            summary_text = f"{(ch.get('title') or '').strip()} {head} … {tail}".strip()
            if not summary_text:
                continue
            doc_id = f"{ref_id}:L2:ch{i}"
            items.append((doc_id, summary_text, {
                "ref_id": ref_id, "title": title,
                "level": "L2", "source_type": "chapter_summary",
                "chapter": i,
                "char_offset": 0,
                "types": ",".join(types_by_chapter.get(i, [])),
            }))

        self._set_progress(ref_id, "L2", total=len(items), done=0, status="running")
        embedded = await self._embed_and_upsert(items)
        self._set_progress(ref_id, "L2", done=embedded, status="done")
        return {"level": "L2", "embedded": embedded}

    async def index_l3(self, ref_id: str, *, resume: bool = True,
                        max_chunks: int = 20_000) -> dict:
        """L3: raw 1500-char chunks. Opt-in; expensive on million-token works.

        Resumable: skips chunks whose stable doc IDs already exist in
        the collection (via VectorStore.get_many) so killing + restarting
        the indexer doesn't re-embed previously persisted batches.
        """
        from rag.reference_db import ReferenceDB
        from rag.chunk_stream import chunk_text_streaming
        rdb = ReferenceDB(self.db_path)
        work = rdb.get_work(ref_id)
        if not work:
            raise ValueError(f"work not found: {ref_id}")
        if not work.get("has_full_text") or not work.get("file_path"):
            self._set_progress(ref_id, "L3", done=0, total=0, status="done")
            return {"level": "L3", "embedded": 0, "skipped": "no full text"}

        title = work.get("title") or ref_id
        self._set_progress(ref_id, "L3", status="running")

        # Map char_offset → chapter number lazily for metadata
        from analysis.feature_extraction.pipeline import FeatureExtractionPipeline
        pipe = FeatureExtractionPipeline(self.db_path)
        full_text = pipe._load_text(work) or ""
        chapters = pipe._split_chapters(full_text)
        # Build chapter-offset map: per-chapter (start_char, end_char) in full_text
        chap_bounds: list[tuple[int, int, int]] = []  # (chap_no, start, end)
        cursor = 0
        for i, ch in enumerate(chapters, start=1):
            content = ch.get("content") or ""
            start_idx = full_text.find(content, cursor)
            if start_idx < 0:
                start_idx = cursor
            end_idx = start_idx + len(content)
            chap_bounds.append((i, start_idx, end_idx))
            cursor = end_idx

        def _chapter_for(offset: int) -> int:
            for chap_no, s, e in chap_bounds:
                if s <= offset < e:
                    return chap_no
            return 0

        # Stream chunks, batch-embed, idempotent upsert
        batch: list[tuple[str, str, dict]] = []
        embedded = 0
        seen = 0
        last_ord = -1
        t_start = time.perf_counter()
        for char_off, ord_, chunk in chunk_text_streaming(work["file_path"]):
            if ord_ >= max_chunks:
                logger.warning(
                    "[work_index] L3 truncated at %d chunks for ref %s",
                    max_chunks, ref_id,
                )
                break
            seen += 1
            doc_id = f"{ref_id}:L3:ord{ord_}"
            content_hash = _short_hash(chunk)
            batch.append((doc_id, chunk, {
                "ref_id": ref_id, "title": title,
                "level": "L3", "source_type": "chapter_chunk",
                "chapter": _chapter_for(char_off),
                "char_offset": char_off,
                "ord": ord_,
                "content_hash": content_hash,
            }))
            last_ord = ord_
            if len(batch) >= self.batch_size:
                embedded += await self._embed_and_upsert_resumable(
                    batch, resume=resume,
                )
                batch = []
                self._set_progress(
                    ref_id, "L3",
                    done=embedded, total=seen,
                    last_ordinal=last_ord,
                )
        if batch:
            embedded += await self._embed_and_upsert_resumable(batch, resume=resume)

        elapsed = round(time.perf_counter() - t_start, 1)
        self._set_progress(
            ref_id, "L3",
            done=embedded, total=seen,
            last_ordinal=last_ord, status="done",
        )
        return {"level": "L3", "embedded": embedded, "seen": seen, "elapsed_s": elapsed}

    async def index_all(self, ref_id: str, *, include_l3: bool = False) -> dict:
        """Index L1 + L2 (and L3 if explicitly requested)."""
        out = {"L1": None, "L2": None, "L3": None}
        try:
            out["L1"] = await self.index_l1(ref_id)
        except Exception as e:
            self._set_progress(ref_id, "L1", status="error", error=str(e)[:300])
            logger.exception("index_l1 failed for %s", ref_id)
        try:
            out["L2"] = await self.index_l2(ref_id)
        except Exception as e:
            self._set_progress(ref_id, "L2", status="error", error=str(e)[:300])
            logger.exception("index_l2 failed for %s", ref_id)
        if include_l3:
            try:
                out["L3"] = await self.index_l3(ref_id)
            except Exception as e:
                self._set_progress(ref_id, "L3", status="error", error=str(e)[:300])
                logger.exception("index_l3 failed for %s", ref_id)
        return out

    def clear_work(self, ref_id: str, levels: list[str] | None = None) -> None:
        """Remove a work's vectors from the collection (cascade-safe)."""
        if levels is None:
            self._vs.delete_where({"ref_id": ref_id})
        else:
            # ChromaDB doesn't support OR clauses cleanly; iterate per level
            for lv in levels:
                self._vs.delete_where({"$and": [
                    {"ref_id": ref_id}, {"level": lv},
                ]})
        with self._open() as c:
            if levels is None:
                c.execute(
                    "DELETE FROM work_index_progress WHERE ref_id=?",
                    (ref_id,),
                )
            else:
                for lv in levels:
                    c.execute(
                        "DELETE FROM work_index_progress "
                        "WHERE ref_id=? AND level=?",
                        (ref_id, lv),
                    )
            c.commit()

    # ── search ──

    async def search(self, query: str, *, k: int = 10,
                      levels: list[str] | None = None,
                      ref_id: str | None = None) -> list[dict]:
        """Two-stage retrieval. Default ``levels=["L1","L2"]`` (stage 1).
        Pass ``levels=["L3"], ref_id=...`` for stage 2 (deep search of a
        single work)."""
        levels = levels or ["L1", "L2"]
        q_vec = await self._emb.embed([query])
        where: dict[str, Any]
        if ref_id:
            where = {"$and": [{"ref_id": ref_id}, {"level": {"$in": levels}}]}
        else:
            where = {"level": {"$in": levels}}
        hits = self._vs.query_with_embedding(q_vec[0], n_results=k, where=where)
        return _filter_by_relevance(hits, k)

    # ── internal ──

    async def _embed_and_upsert(self, items: list[tuple[str, str, dict]]) -> int:
        """Batch-embed + upsert; returns count actually written."""
        if not items:
            return 0
        ids = [it[0] for it in items]
        texts = [it[1] for it in items]
        metas = [it[2] for it in items]
        # Single-batch path — large pages from L1/L2 still fit fine
        vecs = await self._emb.embed(texts)
        self._vs.add_batch_with_embeddings(ids, texts, vecs, metas)
        return len(ids)

    async def _embed_and_upsert_resumable(
        self, items: list[tuple[str, str, dict]], *, resume: bool,
    ) -> int:
        """Like _embed_and_upsert but skips items whose doc_id already
        exists in the collection (for L3 resume)."""
        if not items:
            return 0
        ids = [it[0] for it in items]
        existing: set[str] = set()
        if resume:
            try:
                existing = self._vs.get_many(ids)
            except Exception:
                existing = set()
        fresh = [it for it in items if it[0] not in existing]
        if not fresh:
            return 0
        return await self._embed_and_upsert(fresh)

    @staticmethod
    def _safe_json(raw: str | None) -> Any:
        if not raw:
            return None
        try:
            return json.loads(raw)
        except Exception:
            return None


# ── Convenience factory ─────────────────────────────────────────────


def make_indexer(db_path: str, *, backend: str | None = None) -> WorkIndexer:
    """One-call constructor: pulls the embedding backend from settings
    (or the explicit override) and wires up a ``reference_works`` ChromaDB
    collection keyed by backend name."""
    from models.embedding_provider import get_embedding_provider, collection_name_for
    from rag.vector_store import VectorStore
    provider = get_embedding_provider(backend)
    vs = VectorStore(collection_name=collection_name_for(provider.name))
    return WorkIndexer(db_path, vs, provider)
