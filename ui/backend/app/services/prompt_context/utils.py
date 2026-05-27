"""Shared helpers used by every loader.

- ``clip``    — soft-truncate a string at a character budget
- ``section`` — wrap a non-empty body into a ``## 标题`` block
- ``coerce_json`` — parse a value that may be a JSON string, dict, list or None
- ``parse_rag_excludes`` — turn ``["block::id", ...]`` into ``{block: {ids}}``
- ``cosine`` — vector similarity (zero-fail tolerant)
- ``embed_sync`` — sync bridge into the async embedding provider
- ``estimate_tokens`` — character-count → token estimate (project-wide heuristic)
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import json
import logging
from typing import Any

logger = logging.getLogger("inkoctobot.services.prompt_context.utils")


def clip(text: str, limit: int) -> str:
    """Trim text to a soft character budget with an explicit marker."""
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "\n……（内容过长，已截断）"


def section(label: str, body: str) -> str:
    """Wrap a non-empty body as a labeled block, or return ""."""
    body = (body or "").strip()
    if not body:
        return ""
    return f"\n\n## {label}\n{body}"


def coerce_json(value: Any) -> Any:
    """Parse a value that may be a JSON string, dict, list or None.

    Returns None on any failure — callers should fall back gracefully
    rather than treat a malformed JSON string as fatal.
    """
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        try:
            return json.loads(s)
        except Exception:
            return None
    return None


def parse_rag_excludes(rag_excludes: list[str] | None) -> dict[str, set[str]]:
    """Parse ``["block::id", ...]`` user de-selections into ``{block: {ids}}``."""
    excl: dict[str, set[str]] = {}
    for item in (rag_excludes or []):
        s = str(item or "")
        if "::" not in s:
            continue
        blk, _, iid = s.partition("::")
        excl.setdefault(blk.strip(), set()).add(iid.strip())
    return excl


# CJK character → token ratio. GPT-4 tokenizer averages 1 token per
# ~1.7 CJK characters; ASCII text averages ~1 token per 4 chars.
# This is the project-wide heuristic; not exact, but stable across
# loaders so per-loader telemetry is comparable.
_CJK_TOKEN_RATIO = 1.7


def estimate_tokens(text: str) -> int:
    """Approximate the token count of ``text``.

    CJK-biased heuristic: ``len(text) / 1.7``. Returns ``0`` for empty
    input. Same constant used for total + per-loader telemetry so the
    reported numbers add up.
    """
    if not text:
        return 0
    return int(len(text) / _CJK_TOKEN_RATIO)


def cosine(a: list[float], b: list[float]) -> float:
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


def current_embedding_model_key() -> str:
    """Active ``EmbeddingService`` model_key for cross-model staleness
    checks. Returns ``""`` if the service is unavailable (tests with no
    torch, etc.) so callers can default to "always trust the stored
    embedding" rather than dropping every row.
    """
    try:
        from ui.backend.app.services.embedding import get_embedding_service
        return get_embedding_service().get_current_model().model_key
    except Exception as e:
        logger.debug("embedding service unavailable: %s", e)
        return ""


def embed_sync(texts: list[str]) -> list[list[float]]:
    """Synchronously embed a batch via the global ``EmbeddingService``.

    EMBEDDING_SPEC Phase 2: routes through the spec service so all
    loaders share one model + one cache. Back-compat shape preserved
    (``list[list[float]]``) so existing loader call sites work
    unchanged. Returns ``[[] for _ in texts]`` on any failure so
    callers can degrade gracefully without throwing.
    """
    if not texts:
        return []
    try:
        import numpy as np
        from ui.backend.app.services.embedding import get_embedding_service
        svc = get_embedding_service()
        dim = svc.get_dimension()
        raw = svc.embed_batch(texts)
        out: list[list[float]] = []
        for b in raw:
            if not b:
                out.append([])
                continue
            arr = np.frombuffer(b, dtype="float32")
            if arr.size != dim:
                # Defensive: dimension mismatch from a freshly-switched
                # model that hasn't reloaded yet → degrade per call.
                out.append([])
            else:
                out.append(arr.tolist())
        return out
    except Exception as e:
        logger.debug("embedding failed: %s", e)
        return [[] for _ in texts]
