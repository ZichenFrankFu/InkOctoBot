"""Deterministic in-process embedding stub for Layer A.

Real embeddings need ``sentence-transformers`` which doesn't install
in the sandbox. This stub returns 768-dim vectors derived from a
SHA-256 hash so they're deterministic, comparable, and let cosine
similarity still produce ranks that won't crash downstream code."""
from __future__ import annotations

import contextlib
import hashlib
from typing import Iterator
from unittest import mock


_DIM = 768


def _vec_for(text: str) -> list[float]:
    """Map text → 768-dim float vector. We expand a SHA-256 digest
    via a 4-byte counter so the vector is fully populated; every
    component is in [-1, 1)."""
    h = hashlib.sha256((text or "").encode("utf-8")).digest()
    # Repeat-extend the hash to fill 768 floats.
    needed = _DIM * 4  # 4 bytes per float32-ish
    buf = (h * ((needed // len(h)) + 1))[:needed]
    out: list[float] = []
    for i in range(_DIM):
        chunk = buf[i * 4:(i + 1) * 4]
        n = int.from_bytes(chunk, "big", signed=False)
        out.append((n / 2**31) - 1.0)
    return out


@contextlib.contextmanager
def install_fake_embedding() -> Iterator[None]:
    """Patch the synchronous + async embedding entry points."""
    def _embed_sync(texts):
        return [_vec_for(t) for t in (texts or [])]

    async def _embed_async(texts):
        return _embed_sync(texts)

    patches = [
        mock.patch(
            "ui.backend.app.services.prompt_context.utils.embed_sync",
            side_effect=_embed_sync,
        ),
        # The current_embedding_model_key helper is unrelated to
        # embedding *computation*; we leave it as-is so the index
        # row carries a sensible model key.
    ]
    # Patch EmbeddingService.embed_batch when available — the indexer
    # uses it via the service.
    try:
        from ui.backend.app.services.embedding_service import EmbeddingService
        patches.append(
            mock.patch.object(EmbeddingService, "embed_batch",
                              side_effect=_embed_sync)
        )
    except Exception:
        pass

    for p in patches:
        p.start()
    try:
        yield
    finally:
        for p in patches:
            p.stop()
