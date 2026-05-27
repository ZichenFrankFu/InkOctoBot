"""Tests for the EmbeddingService singleton + cache + switch flow."""
from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import numpy as np

from ui.backend.app.services.embedding import (
    EmbeddingService, SwitchResult,
)
from ui.backend.app.services.embedding.providers.base import (
    EmbeddingProvider,
)
from ui.backend.app.services.embedding.registry import get_model


class _MockProvider(EmbeddingProvider):
    """In-memory provider that mocks load + returns deterministic vectors."""

    def __init__(self, model, device: str = "cpu", *, fp16: bool = False) -> None:
        self._model = model
        self._device = device
        self.calls = 0
        self.loaded = False

    def load(self) -> None:
        self.loaded = True

    def unload(self) -> None:
        self.loaded = False

    def embed(self, texts, normalize: bool = True):
        self.calls += 1
        # Deterministic: same text → same vector (per process).
        return np.array(
            [[float(abs(hash(t)) % 1000) / 1000.0] * self._model.dimension
             for t in texts],
            dtype="float32",
        )

    def get_device(self) -> str:
        return self._device

    @property
    def dimension(self) -> int:
        return self._model.dimension

    @property
    def is_loaded(self) -> bool:
        return self.loaded


def _patch_provider():
    """Replace the real TransformersProvider with the mock everywhere
    the service module instantiates it."""
    return mock.patch(
        "ui.backend.app.services.embedding.service.TransformersProvider",
        _MockProvider,
    )


class TestSingleton(unittest.TestCase):

    def setUp(self) -> None:
        EmbeddingService._reset_for_tests()

    def test_get_returns_same_instance(self) -> None:
        with _patch_provider():
            a = EmbeddingService.get()
            b = EmbeddingService.get()
        self.assertIs(a, b)


class TestEmbed(unittest.TestCase):

    def setUp(self) -> None:
        EmbeddingService._reset_for_tests()

    def test_embed_single_returns_bytes(self) -> None:
        with _patch_provider():
            svc = EmbeddingService.get()
            out = svc.embed("你好世界")
        self.assertIsInstance(out, bytes)
        # 768-dim × 4 bytes per float32
        self.assertEqual(len(out), 768 * 4)

    def test_embed_batch_returns_aligned_list(self) -> None:
        with _patch_provider():
            svc = EmbeddingService.get()
            out = svc.embed_batch(["a", "b", "c"])
        self.assertEqual(len(out), 3)
        for b in out:
            self.assertEqual(len(b), 768 * 4)

    def test_empty_text_returns_empty_bytes(self) -> None:
        with _patch_provider():
            svc = EmbeddingService.get()
            self.assertEqual(svc.embed_batch([]), [])


class TestTextHash(unittest.TestCase):

    def setUp(self) -> None:
        EmbeddingService._reset_for_tests()

    def test_hash_is_deterministic(self) -> None:
        with _patch_provider():
            svc = EmbeddingService.get()
            self.assertEqual(svc.compute_text_hash("abc"),
                              svc.compute_text_hash("abc"))

    def test_hash_match(self) -> None:
        with _patch_provider():
            svc = EmbeddingService.get()
            h = svc.compute_text_hash("hello")
            self.assertTrue(svc.is_text_hash_match(h, "hello"))
            self.assertFalse(svc.is_text_hash_match(h, "world"))


class TestCache(unittest.TestCase):

    def setUp(self) -> None:
        EmbeddingService._reset_for_tests()

    def test_cache_avoids_reembed(self) -> None:
        with _patch_provider():
            svc = EmbeddingService.get()
            svc.embed("repeat me")
            svc.embed("repeat me")
            svc.embed("repeat me")
            # Mock provider counts calls — should be exactly 1 batch
            # (3 lookups all hit cache after the first one).
            self.assertEqual(svc._provider.calls, 1)

    def test_cache_isolated_per_model(self) -> None:
        with _patch_provider():
            svc = EmbeddingService.get()
            svc.embed("x")
            self.assertEqual(svc._cache_size(), 1)
            svc.switch_model("bge-large-zh")
            self.assertEqual(svc._cache_size(), 0)


class TestSwitchModel(unittest.TestCase):

    def setUp(self) -> None:
        EmbeddingService._reset_for_tests()

    def test_switch_returns_need_reindex_when_dimension_changes(self) -> None:
        with _patch_provider():
            svc = EmbeddingService.get()
            res = svc.switch_model("bge-large-zh")
        self.assertIsInstance(res, SwitchResult)
        self.assertTrue(res.success)
        self.assertEqual(res.previous_model_key, "bge-base-zh")
        self.assertEqual(res.model_key, "bge-large-zh")
        self.assertTrue(res.need_reindex)  # 768 → 1024
        self.assertGreater(len(res.affected_tables), 0)

    def test_switch_same_model_is_noop(self) -> None:
        with _patch_provider():
            svc = EmbeddingService.get()
            res = svc.switch_model("bge-base-zh")
        self.assertFalse(res.need_reindex)

    def test_switch_changes_current_model_and_dimension(self) -> None:
        with _patch_provider():
            svc = EmbeddingService.get()
            self.assertEqual(svc.get_dimension(), 768)
            svc.switch_model("text2vec-base-multilingual")
            self.assertEqual(svc.get_dimension(), 384)
            self.assertEqual(svc.get_current_model().model_key,
                              "text2vec-base-multilingual")

    def test_switch_unloads_old_provider(self) -> None:
        with _patch_provider():
            svc = EmbeddingService.get()
            svc.embed("warm up")
            old_provider = svc._provider
            self.assertTrue(old_provider.is_loaded)
            svc.switch_model("bge-large-zh")
            self.assertFalse(old_provider.is_loaded)


class TestEstimateLoadTime(unittest.TestCase):

    def setUp(self) -> None:
        EmbeddingService._reset_for_tests()

    def test_estimate_proportional_to_size(self) -> None:
        with _patch_provider():
            svc = EmbeddingService.get()
            small = svc.estimate_load_time()  # bge-base-zh: 400 MB
            svc.switch_model("qwen3-embedding-8b")
            big = svc.estimate_load_time()    # 16000 MB
        self.assertGreater(big, small)


if __name__ == "__main__":
    unittest.main()
