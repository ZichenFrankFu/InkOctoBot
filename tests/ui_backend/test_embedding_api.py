"""Tests for the embedding API (Phase 1 read-only endpoints)."""
from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from ui.backend.app.services.embedding import (
    EmbeddingService, hardware_detector as hw,
)


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


if __name__ == "__main__":
    unittest.main()
