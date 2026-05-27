"""Tests for TransformersProvider's OOM-fallback behavior (EMBEDDING_SPEC § 5 + § 11).

Nothing here actually loads weights — we stub ``sentence_transformers``
in ``sys.modules`` and verify the provider's branching:

- GPU OOM → fall back to CPU and surface the downgrade in
  ``load_warnings``.
- Non-CUDA-related load failures propagate (not silently swallowed).
- ``embed`` empty list returns ``(0, dim)`` array (spec § 4.3).
- ``unload`` clears state.
"""
from __future__ import annotations

import os
import sys
import types
import unittest
from typing import Any

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import numpy as np

from ui.backend.app.services.embedding.providers.transformers import (
    TransformersProvider,
)
from ui.backend.app.services.embedding.registry import get_model


class _StubST:
    """Stand-in for sentence_transformers.SentenceTransformer.

    Per-test code injects a ``factory`` that decides whether each
    construction succeeds or raises — letting us script OOM scenarios."""

    factory = None

    def __init__(self, hf_repo: str, *, device: str = "cpu",
                 model_kwargs: dict | None = None) -> None:
        if self.__class__.factory is not None:
            self.__class__.factory(hf_repo, device, model_kwargs)
        self.hf_repo = hf_repo
        self.device = device
        self.model_kwargs = model_kwargs
        self._dim = 768

    def encode(self, texts, **kwargs) -> Any:
        return np.ones((len(texts), self._dim), dtype="float32")


def _install_stub_sentence_transformers() -> None:
    """Inject a fake sentence_transformers module so the real lib
    (not present in the test env) isn't required."""
    mod = types.ModuleType("sentence_transformers")
    mod.SentenceTransformer = _StubST  # type: ignore[attr-defined]
    sys.modules["sentence_transformers"] = mod


def _uninstall_stub() -> None:
    sys.modules.pop("sentence_transformers", None)


class TestOOMFallback(unittest.TestCase):
    """The spec says: GPU OOM during load → fall back to CPU + warn."""

    def setUp(self) -> None:
        _install_stub_sentence_transformers()
        _StubST.factory = None
        self.model = get_model("bge-base-zh")

    def tearDown(self) -> None:
        _StubST.factory = None
        _uninstall_stub()

    def test_oom_on_cuda_falls_back_to_cpu(self) -> None:
        calls: list[str] = []

        def factory(hf_repo: str, device: str, model_kwargs):
            calls.append(device)
            if device == "cuda":
                raise RuntimeError("CUDA out of memory")

        _StubST.factory = factory
        provider = TransformersProvider(self.model, device="cuda", fp16=True)
        provider.load()

        # First call cuda (raises), second call cpu (succeeds).
        self.assertEqual(calls, ["cuda", "cpu"])
        self.assertEqual(provider.get_device(), "cpu")
        self.assertTrue(provider.is_loaded)
        self.assertTrue(provider.load_warnings)
        self.assertIn("CPU", provider.load_warnings[0])

    def test_oom_with_generic_cuda_word_also_falls_back(self) -> None:
        """The fallback trigger is liberal — any error string mentioning
        cuda/device/oom on a cuda placement counts as 'CPU is safer'."""
        def factory(hf_repo: str, device: str, model_kwargs):
            if device == "cuda":
                raise RuntimeError("CUDA device init failed")

        _StubST.factory = factory
        provider = TransformersProvider(self.model, device="cuda")
        provider.load()
        self.assertEqual(provider.get_device(), "cpu")

    def test_non_cuda_error_propagates(self) -> None:
        """A model-not-found or auth error should NOT be swallowed —
        the user needs to know."""
        def factory(hf_repo: str, device: str, model_kwargs):
            raise FileNotFoundError("HF repo not found: BAAI/bge-base-zh-v1.5")

        _StubST.factory = factory
        provider = TransformersProvider(self.model, device="cpu")
        with self.assertRaises(FileNotFoundError):
            provider.load()
        self.assertFalse(provider.is_loaded)

    def test_cpu_only_load_does_not_attempt_fallback(self) -> None:
        """If we already requested CPU, an error means something else
        is wrong — don't try to 'fall back' to where we are."""
        calls: list[str] = []

        def factory(hf_repo: str, device: str, model_kwargs):
            calls.append(device)
            raise RuntimeError("CUDA out of memory")  # weird but possible

        _StubST.factory = factory
        provider = TransformersProvider(self.model, device="cpu")
        with self.assertRaises(RuntimeError):
            provider.load()
        # Only one attempt — no fallback loop.
        self.assertEqual(calls, ["cpu"])


class TestProviderEmbed(unittest.TestCase):

    def setUp(self) -> None:
        _install_stub_sentence_transformers()
        _StubST.factory = None
        self.model = get_model("bge-base-zh")

    def tearDown(self) -> None:
        _StubST.factory = None
        _uninstall_stub()

    def test_embed_empty_returns_empty_array(self) -> None:
        provider = TransformersProvider(self.model, device="cpu")
        out = provider.embed([])
        self.assertEqual(out.shape, (0, self.model.dimension))
        self.assertEqual(out.dtype, np.dtype("float32"))

    def test_embed_lazy_loads_on_first_call(self) -> None:
        provider = TransformersProvider(self.model, device="cpu")
        self.assertFalse(provider.is_loaded)
        out = provider.embed(["x", "y"])
        self.assertTrue(provider.is_loaded)
        self.assertEqual(out.shape, (2, self.model.dimension))

    def test_dimension_matches_model(self) -> None:
        provider = TransformersProvider(self.model, device="cpu")
        self.assertEqual(provider.dimension, self.model.dimension)


class TestNaNAndZeroDetection(unittest.TestCase):
    """Spec § 11.4: NaN / all-zero vectors are corruption, not data.
    The provider raises so the caller's per-row error handler can
    isolate the offending text instead of persisting garbage."""

    def setUp(self) -> None:
        _install_stub_sentence_transformers()
        _StubST.factory = None
        self.model = get_model("bge-base-zh")

    def tearDown(self) -> None:
        _StubST.factory = None
        _uninstall_stub()

    def test_nan_vector_raises(self) -> None:
        class _NaNStub(_StubST):
            def encode(self, texts, **kwargs):
                arr = np.ones((len(texts), self._dim), dtype="float32")
                arr[0, 0] = float("nan")
                return arr

        sys.modules["sentence_transformers"].SentenceTransformer = _NaNStub
        provider = TransformersProvider(self.model, device="cpu")
        with self.assertRaisesRegex(RuntimeError, "NaN"):
            provider.embed(["ok text"])

    def test_all_zero_vector_raises(self) -> None:
        class _ZeroStub(_StubST):
            def encode(self, texts, **kwargs):
                return np.zeros((len(texts), self._dim), dtype="float32")

        sys.modules["sentence_transformers"].SentenceTransformer = _ZeroStub
        provider = TransformersProvider(self.model, device="cpu")
        with self.assertRaisesRegex(RuntimeError, "all-zero"):
            provider.embed(["ok text"])

    def test_mixed_zero_row_raises_with_indices(self) -> None:
        """If only one row of a batch is degenerate, the error names
        the offending row so the caller can act on it."""
        class _MixedStub(_StubST):
            def encode(self, texts, **kwargs):
                arr = np.ones((len(texts), self._dim), dtype="float32")
                if len(texts) >= 2:
                    arr[1] = 0.0
                return arr

        sys.modules["sentence_transformers"].SentenceTransformer = _MixedStub
        provider = TransformersProvider(self.model, device="cpu")
        with self.assertRaisesRegex(RuntimeError, r"\[1\]"):
            provider.embed(["good", "bad"])


class TestUnload(unittest.TestCase):

    def setUp(self) -> None:
        _install_stub_sentence_transformers()
        _StubST.factory = None
        self.model = get_model("bge-base-zh")

    def tearDown(self) -> None:
        _StubST.factory = None
        _uninstall_stub()

    def test_unload_clears_state(self) -> None:
        provider = TransformersProvider(self.model, device="cpu")
        provider.load()
        self.assertTrue(provider.is_loaded)
        provider.unload()
        self.assertFalse(provider.is_loaded)

    def test_unload_is_idempotent(self) -> None:
        provider = TransformersProvider(self.model, device="cpu")
        provider.unload()  # never loaded
        provider.unload()  # still never loaded
        self.assertFalse(provider.is_loaded)


if __name__ == "__main__":
    unittest.main()
