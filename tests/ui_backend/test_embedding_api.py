"""Tests for the embedding API (Phase 1 read-only + Phase 3 write surface)."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from ui.backend.app import settings as settings_module
from ui.backend.app.services.embedding import (
    EmbeddingService, batch_reindex, hardware_detector as hw,
)
from ui.backend.app.services.embedding.providers.base import EmbeddingProvider


class TestEmbeddingAPI(unittest.TestCase):

    def setUp(self) -> None:
        EmbeddingService._reset_for_tests()
        hw.reset_cache()
        # Force a known hardware probe (no real GPU in this env).
        self._patches = [
            mock.patch.object(hw, "_probe_cuda", return_value=(False, 0, "")),
            mock.patch.object(hw, "_probe_mps", return_value=False),
            mock.patch.object(hw, "_probe_ram_mb", return_value=8192),
        ]
        for p in self._patches:
            p.start()
        from ui.backend.app.routers.embedding_api import router
        app = FastAPI()
        app.include_router(router)
        self.client = TestClient(app)

    def tearDown(self) -> None:
        for p in self._patches:
            p.stop()
        EmbeddingService._reset_for_tests()
        hw.reset_cache()

    def test_get_models_returns_seven(self) -> None:
        res = self.client.get("/api/embedding/models")
        self.assertEqual(res.status_code, 200)
        models = res.json()["models"]
        self.assertEqual(len(models), 7)
        # required field shape
        for m in models:
            for k in ("model_key", "hf_repo", "language", "dimension",
                      "min_vram_mb", "is_default_zh", "is_default_en"):
                self.assertIn(k, m)

    def test_get_current_returns_default(self) -> None:
        res = self.client.get("/api/embedding/current")
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertEqual(body["model"]["model_key"], "bge-base-zh")
        self.assertEqual(body["dimension"], 768)
        self.assertFalse(body["is_ready"])  # nothing loaded yet
        self.assertGreater(body["estimate_load_seconds"], 0)

    def test_hardware_status_shape(self) -> None:
        res = self.client.get("/api/embedding/hardware-status")
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertIn("hardware", body)
        self.assertIn("model_compatibility", body)
        self.assertEqual(body["hardware"]["has_cuda"], False)
        self.assertEqual(body["hardware"]["ram_mb"], 8192)
        # Every registered model appears in the compat list.
        keys = {c["model_key"] for c in body["model_compatibility"]}
        self.assertIn("bge-base-zh", keys)
        self.assertIn("qwen3-embedding-8b", keys)

    def test_hardware_status_warns_for_oversized_model(self) -> None:
        res = self.client.get("/api/embedding/hardware-status")
        compat = {c["model_key"]: c for c in res.json()["model_compatibility"]}
        # No GPU → every model is "would_run_on": "cpu".
        for c in compat.values():
            self.assertEqual(c["would_run_on"], "cpu")
        # Qwen3-8B has min_ram > 8GB so we should see a swap warning.
        self.assertTrue(compat["qwen3-embedding-8b"]["warnings"])


# ── Phase 3 write surface ────────────────────────────────────────────


class _MockProvider(EmbeddingProvider):
    def __init__(self, model, device: str = "cpu", *, fp16: bool = False) -> None:
        self._model = model
        self._loaded = False

    def load(self) -> None:
        self._loaded = True

    def unload(self) -> None:
        self._loaded = False

    def embed(self, texts, normalize: bool = True):
        return np.ones((len(texts), self._model.dimension), dtype="float32")

    def get_device(self) -> str:
        return "cpu"

    @property
    def dimension(self) -> int:
        return self._model.dimension

    @property
    def is_loaded(self) -> bool:
        return self._loaded


def _patch_provider():
    return mock.patch(
        "ui.backend.app.services.embedding.service.TransformersProvider",
        _MockProvider,
    )


class TestSwitchEndpoint(unittest.TestCase):

    def setUp(self) -> None:
        EmbeddingService._reset_for_tests()
        hw.reset_cache()
        self._hw_patches = [
            mock.patch.object(hw, "_probe_cuda", return_value=(False, 0, "")),
            mock.patch.object(hw, "_probe_mps", return_value=False),
            mock.patch.object(hw, "_probe_ram_mb", return_value=8192),
        ]
        for p in self._hw_patches:
            p.start()
        self._tmp = tempfile.mkdtemp()
        self._settings_patch = mock.patch.object(
            settings_module, "_embedding_settings_path",
            return_value=Path(self._tmp) / "settings.json",
        )
        self._settings_patch.start()
        self._provider_patch = _patch_provider()
        self._provider_patch.start()
        from ui.backend.app.routers.embedding_api import router
        app = FastAPI()
        app.include_router(router)
        self.client = TestClient(app)

    def tearDown(self) -> None:
        self._provider_patch.stop()
        self._settings_patch.stop()
        for p in self._hw_patches:
            p.stop()
        EmbeddingService._reset_for_tests()
        hw.reset_cache()

    def test_switch_to_known_model(self) -> None:
        res = self.client.post(
            "/api/embedding/switch", json={"model_key": "bge-large-zh"},
        )
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertTrue(body["success"])
        self.assertEqual(body["model_key"], "bge-large-zh")
        self.assertEqual(body["previous_model_key"], "bge-base-zh")
        # Dimension change → need_reindex True.
        self.assertTrue(body["need_reindex"])

    def test_switch_to_same_model_is_noop(self) -> None:
        res = self.client.post(
            "/api/embedding/switch", json={"model_key": "bge-base-zh"},
        )
        self.assertEqual(res.status_code, 200)
        self.assertFalse(res.json()["need_reindex"])

    def test_switch_unknown_model_rejected(self) -> None:
        res = self.client.post(
            "/api/embedding/switch", json={"model_key": "no-such-model"},
        )
        self.assertEqual(res.status_code, 400)

    def test_switch_missing_key_rejected(self) -> None:
        res = self.client.post("/api/embedding/switch", json={})
        self.assertEqual(res.status_code, 400)


class TestReindexEndpoints(unittest.TestCase):
    """The reindex worker is fully covered in ``test_batch_reindex.py``.
    Here we lock the HTTP wrapper's contract: status codes + payload shape."""

    def setUp(self) -> None:
        EmbeddingService._reset_for_tests()
        batch_reindex.reset_for_tests()
        hw.reset_cache()
        self._hw_patches = [
            mock.patch.object(hw, "_probe_cuda", return_value=(False, 0, "")),
            mock.patch.object(hw, "_probe_mps", return_value=False),
            mock.patch.object(hw, "_probe_ram_mb", return_value=8192),
        ]
        for p in self._hw_patches:
            p.start()
        from ui.backend.app.routers.embedding_api import router
        app = FastAPI()
        app.include_router(router)
        self.client = TestClient(app)

    def tearDown(self) -> None:
        for p in self._hw_patches:
            p.stop()
        EmbeddingService._reset_for_tests()
        batch_reindex.reset_for_tests()
        hw.reset_cache()

    def test_start_reindex_returns_task_id(self) -> None:
        with mock.patch.object(
            batch_reindex, "start_reindex", return_value="rx_fake",
        ) as m:
            res = self.client.post("/api/embedding/reindex", json={})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json(), {"task_id": "rx_fake", "state": "running"})
        m.assert_called_once_with(target_model_key=None)

    def test_start_reindex_with_explicit_model(self) -> None:
        with mock.patch.object(
            batch_reindex, "start_reindex", return_value="rx_q",
        ) as m:
            res = self.client.post(
                "/api/embedding/reindex", json={"model_key": "bge-large-zh"},
            )
        self.assertEqual(res.status_code, 200)
        m.assert_called_once_with(target_model_key="bge-large-zh")

    def test_start_reindex_rejects_unknown_model(self) -> None:
        res = self.client.post(
            "/api/embedding/reindex", json={"model_key": "no-such-model"},
        )
        self.assertEqual(res.status_code, 400)

    def test_status_unknown_task_returns_404(self) -> None:
        res = self.client.get("/api/embedding/reindex/rx_nope")
        self.assertEqual(res.status_code, 404)

    def test_status_known_task_returns_progress(self) -> None:
        fake_status = {
            "task_id": "rx_x", "state": "completed",
            "target_model_key": "bge-base-zh",
            "progress": {"overall_total": 0, "overall_current": 0,
                          "table_progress": {}},
            "eta_seconds": None, "started_at": 1.0, "errors": [],
            "error_count": 0,
        }
        with mock.patch.object(
            batch_reindex, "get_status", return_value=fake_status,
        ):
            res = self.client.get("/api/embedding/reindex/rx_x")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["state"], "completed")

    def test_cancel_unknown_task_returns_404(self) -> None:
        res = self.client.post("/api/embedding/reindex/rx_nope/cancel")
        self.assertEqual(res.status_code, 404)

    def test_cancel_known_task(self) -> None:
        with mock.patch.object(batch_reindex, "cancel", return_value=True):
            res = self.client.post("/api/embedding/reindex/rx_x/cancel")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json(), {"ok": True, "task_id": "rx_x"})


if __name__ == "__main__":
    unittest.main()
