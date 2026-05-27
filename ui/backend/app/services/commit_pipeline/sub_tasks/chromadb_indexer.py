"""chromadb_indexer — chunk + embed + upsert chapter into ChromaDB.

Convention: collection name = ``project_<project_id>_chunks_<model_key>``.
This deliberately diverges from the legacy backend-only naming so
chapter chunks stay project-isolated and model-versioned. Old
collections become orphans; Phase 2 design accepted that (chapter
chunks are derivable, not source of truth).

Chunking: 400-character windows with 80-character overlap (per spec).
Sentence-aware boundary nudging is deferred — the boundary clipper
is good enough for embedding similarity.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from .summarizer import _resolve_chapter_num, _resolve_chapter_text

logger = logging.getLogger("inkoctobot.commit_pipeline.chromadb_indexer")

if TYPE_CHECKING:
    from . import SubTaskContext


_CHUNK_SIZE = 400
_CHUNK_OVERLAP = 80


def _chunk(text: str, size: int = _CHUNK_SIZE, overlap: int = _CHUNK_OVERLAP) -> list[str]:
    """Sliding-window chunker. Returns non-empty stripped chunks."""
    text = text.replace("\r\n", "\n")
    if not text or len(text) <= size:
        return [text.strip()] if text.strip() else []
    step = max(1, size - overlap)
    chunks: list[str] = []
    i = 0
    while i < len(text):
        chunk = text[i:i + size].strip()
        if chunk:
            chunks.append(chunk)
        if i + size >= len(text):
            break
        i += step
    return chunks


def _collection_name(project_id: str, model_key: str) -> str:
    """Project-isolated, model-versioned ChromaDB collection name.

    ChromaDB collection-name restrictions (3–63 chars, [a-z0-9._-]):
    project_id may contain other chars, so sanitize defensively.
    """
    import re
    safe_pid = re.sub(r"[^a-z0-9._-]", "_", project_id.lower())[:30]
    safe_model = re.sub(r"[^a-z0-9._-]", "_", model_key.lower())[:20]
    return f"project_{safe_pid}_chunks_{safe_model}"


async def run(ctx: "SubTaskContext") -> dict[str, Any]:
    chapter_text = _resolve_chapter_text(ctx)
    if not chapter_text.strip():
        return {"status": "skipped", "reason": "empty_chapter_text"}

    chunks = _chunk(chapter_text)
    if not chunks:
        return {"status": "skipped", "reason": "no_chunks"}

    chapter_num = _resolve_chapter_num(ctx)

    # Embed via the shared EmbeddingService so model-key + cache stay
    # in lockstep with the rest of the system.
    from ui.backend.app.services.embedding import get_embedding_service
    svc = get_embedding_service()
    model = svc.get_current_model()
    raw = svc.embed_batch(chunks)
    import numpy as np
    embeddings = [np.frombuffer(b, dtype="float32").tolist() for b in raw if b]
    if len(embeddings) != len(chunks):
        raise RuntimeError(
            f"embedding count mismatch: {len(embeddings)} vs {len(chunks)} chunks"
        )

    # Upsert. Wrap chromadb import inside the function so the pipeline
    # doesn't pull it at module load — the embedding stack is heavy.
    from knowledge.vector_store import VectorStore
    collection = _collection_name(ctx.project_id, model.model_key)
    store = VectorStore(collection_name=collection)
    ids = [f"{ctx.chapter_id}:chunk:{i:04d}" for i in range(len(chunks))]
    metadatas = [{
        "project_id":  ctx.project_id,
        "chapter_id":  ctx.chapter_id,
        "chapter_num": chapter_num,
        "chunk_index": i,
        "model_key":   model.model_key,
    } for i in range(len(chunks))]
    store.add_batch_with_embeddings(
        ids=ids, texts=chunks, embeddings=embeddings, metadatas=metadatas,
    )

    return {
        "status":          "ok",
        "chunks":          len(chunks),
        "collection_name": collection,
        "model_key":       model.model_key,
    }
