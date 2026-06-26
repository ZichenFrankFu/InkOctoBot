"""LocalSTProvider → TfidfFallbackProvider auto-downgrade.

Regression: a broken sentence-transformers env (e.g. transformers < 4.41
missing ``EncoderDecoderCache``) used to 500 every /api/references/.../index/run
call. The factory now wraps the local provider with `_LocalWithFallback`
which probes at first access and commits to TF-IDF if the probe fails,
so indexing stays working at degraded quality.
"""
from __future__ import annotations

import asyncio

import numpy as np
import pytest

from llm import embedding_provider as ep


def test_tfidf_fallback_produces_normalized_vectors() -> None:
    prov = ep.TfidfFallbackProvider()
    arr = asyncio.run(prov.embed(["主角觉醒能力", "宿命对决"]))
    assert arr.shape == (2, prov.dim)
    norms = np.linalg.norm(arr, axis=1)
    np.testing.assert_allclose(norms, 1.0, atol=1e-5)


def test_local_with_fallback_commits_to_tfidf_when_st_breaks(monkeypatch) -> None:
    """Simulate the EncoderDecoderCache scenario: SentenceTransformer
    instantiation raises ImportError, the wrapper should swap in TF-IDF and
    keep `embed()` working."""
    ep.clear_embedding_provider_cache()

    def _boom(*args, **kwargs):
        raise ImportError("cannot import EncoderDecoderCache")

    # Patch the ST class lookup inside LocalSTProvider._ensure
    import sys, types
    fake_st_mod = types.ModuleType("sentence_transformers")
    fake_st_mod.SentenceTransformer = _boom
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake_st_mod)

    wrapper = ep._LocalWithFallback()
    # First access triggers the probe; the wrapper should swap to TF-IDF
    # silently so callers see a working provider.
    assert wrapper.dim == 384
    assert "tfidf" in wrapper.name
    arr = asyncio.run(wrapper.embed(["a b c", "d e f"]))
    assert arr.shape == (2, 384)
    assert not np.isnan(arr).any()


def test_factory_caches_resolved_provider(monkeypatch, tmp_path) -> None:
    """Repeated factory calls within a process must return the same
    instance so the ST model isn't reloaded per request."""
    ep.clear_embedding_provider_cache()
    # Point settings_path at a non-existent file so we don't pull real config
    monkeypatch.setattr(ep, "logger", ep.logger)  # noqa: keep
    p1 = ep.get_embedding_provider("tfidf")
    p2 = ep.get_embedding_provider("tfidf")
    assert p1 is p2
    ep.clear_embedding_provider_cache()
    p3 = ep.get_embedding_provider("tfidf")
    assert p3 is not p1


def test_local_strict_does_not_swallow_errors(monkeypatch) -> None:
    """`backend=local_strict` is the diagnostic path — it must propagate the
    real error instead of silently downgrading, so users see exactly what's
    wrong with their env."""
    ep.clear_embedding_provider_cache()
    import sys, types
    fake_st_mod = types.ModuleType("sentence_transformers")

    def _boom(*args, **kwargs):
        raise ImportError("cannot import EncoderDecoderCache")

    fake_st_mod.SentenceTransformer = _boom
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake_st_mod)

    strict = ep.get_embedding_provider("local_strict")
    with pytest.raises(ImportError):
        asyncio.run(strict.embed(["x"]))
