"""Tests for the language-mode toggle (EMBEDDING_SPEC § 3 + § 8).

The contract under test:
- ``POST /api/settings/embedding-language-mode`` forces ``model_key`` to
  the target language's default and persists both keys to
  ``data/settings.json``.
- ``GET`` reports the current language + persisted model.
- The persisted state is honored by ``EmbeddingService._build_from_settings``
  on the next process startup.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import numpy as np

from ui.backend.app import settings as settings_module
from ui.backend.app.services.embedding import (
    EmbeddingService, hardware_detector as hw,
)
from ui.backend.app.services.embedding.providers.base import EmbeddingProvider
from ui.backend.app.services.embedding.registry import default_for_language


class _MockProvider(EmbeddingProvider):
    def __init__(self, model, device: str = "cpu", *, fp16: bool = False) -> None:
        self._model = model
        self._device = device
        self._loaded = False

    def load(self) -> None:
        self._loaded = True

    def unload(self) -> None:
        self._loaded = False

    def embed(self, texts, normalize: bool = True):
        return np.ones((len(texts), self._model.dimension), dtype="float32")

    def get_device(self) -> str:
        return self._device

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


class _IsolatedSettingsMixin:
    """Redirect settings.json to a per-test tempdir so the toggle
    endpoint can't leak into the dev environment's settings file."""

    def _isolate_settings(self) -> Path:
        self._tmp = tempfile.mkdtemp()
        path = Path(self._tmp) / "settings.json"
        self._settings_patch = mock.patch.object(
            settings_module, "_embedding_settings_path", return_value=path,
        )
        self._settings_patch.start()
        return path

    def _release_settings(self) -> None:
        self._settings_patch.stop()


def _build_client():
    """Mount BOTH routers — the language-mode endpoint lives on the
    /api/settings sub-router."""
    from ui.backend.app.routers.embedding_api import router, settings_router
    app = FastAPI()
    app.include_router(router)
    app.include_router(settings_router)
    return TestClient(app)


class TestSetLanguageMode(unittest.TestCase, _IsolatedSettingsMixin):

    def setUp(self) -> None:
        EmbeddingService._reset_for_tests()
        hw.reset_cache()
        # Force CPU so device_decision is deterministic.
        self._hw_patches = [
            mock.patch.object(hw, "_probe_cuda", return_value=(False, 0, "")),
            mock.patch.object(hw, "_probe_mps", return_value=False),
            mock.patch.object(hw, "_probe_ram_mb", return_value=8192),
        ]
        for p in self._hw_patches:
            p.start()
        self._json_path = self._isolate_settings()
        self._provider_patch = _patch_provider()
        self._provider_patch.start()
        self.client = _build_client()

    def tearDown(self) -> None:
        self._provider_patch.stop()
        for p in self._hw_patches:
            p.stop()
        self._release_settings()
        EmbeddingService._reset_for_tests()
        hw.reset_cache()

    def test_zh_to_en_switches_model_to_bge_m3(self) -> None:
        res = self.client.post(
            "/api/settings/embedding-language-mode",
            json={"language_mode": "en"},
        )
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertEqual(body["language_mode"], "en")
        self.assertEqual(body["model_key"], default_for_language("en").model_key)
        self.assertEqual(body["switch_result"]["previous_model_key"], "bge-base-zh")
        self.assertTrue(body["switch_result"]["need_reindex"])
        # Active service really changed.
        self.assertEqual(
            EmbeddingService.get().get_current_model().model_key,
            "bge-m3",
        )

    def test_en_to_zh_returns_to_bge_base_zh(self) -> None:
        # Start in en.
        self.client.post(
            "/api/settings/embedding-language-mode",
            json={"language_mode": "en"},
        )
        # Now flip back.
        res = self.client.post(
            "/api/settings/embedding-language-mode",
            json={"language_mode": "zh"},
        )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["model_key"], "bge-base-zh")

    def test_same_language_force_switches_to_default(self) -> None:
        """Spec § 3: ``切 language_mode 时强制 model_key 切到对应语言默认``.

        Even if the user is already on the right language but a
        NON-default model of that language, the toggle must force them
        back to the language default."""
        # Move to bge-large-zh (non-default zh model) directly.
        EmbeddingService.get().switch_model("bge-large-zh")
        res = self.client.post(
            "/api/settings/embedding-language-mode",
            json={"language_mode": "zh"},
        )
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertEqual(body["model_key"], "bge-base-zh")
        self.assertEqual(body["switch_result"]["previous_model_key"], "bge-large-zh")
        self.assertTrue(body["switch_result"]["need_reindex"])

    def test_invalid_mode_rejected(self) -> None:
        res = self.client.post(
            "/api/settings/embedding-language-mode",
            json={"language_mode": "ja"},
        )
        self.assertEqual(res.status_code, 400)

    def test_empty_mode_rejected(self) -> None:
        res = self.client.post(
            "/api/settings/embedding-language-mode",
            json={"language_mode": ""},
        )
        self.assertEqual(res.status_code, 400)

    def test_missing_key_rejected(self) -> None:
        res = self.client.post(
            "/api/settings/embedding-language-mode",
            json={},
        )
        self.assertEqual(res.status_code, 400)

    def test_settings_json_gets_written(self) -> None:
        self.client.post(
            "/api/settings/embedding-language-mode",
            json={"language_mode": "en"},
        )
        self.assertTrue(self._json_path.exists())
        blob = json.loads(self._json_path.read_text("utf-8"))
        self.assertEqual(blob["embedding_language_mode"], "en")
        self.assertEqual(blob["embedding_model_key"], "bge-m3")

    def test_affected_tables_returned_for_reindex_planning(self) -> None:
        res = self.client.post(
            "/api/settings/embedding-language-mode",
            json={"language_mode": "en"},
        )
        tables = res.json()["switch_result"]["affected_tables"]
        for t in ("worldbook_entries", "skill_index", "subplot_threads",
                  "reference_chapters", "inspirations"):
            self.assertIn(t, tables)


class TestGetLanguageMode(unittest.TestCase, _IsolatedSettingsMixin):

    def setUp(self) -> None:
        EmbeddingService._reset_for_tests()
        self._json_path = self._isolate_settings()
        self.client = _build_client()

    def tearDown(self) -> None:
        self._release_settings()
        EmbeddingService._reset_for_tests()

    def test_default_when_no_settings_file(self) -> None:
        res = self.client.get("/api/settings/embedding-language-mode")
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertEqual(body["language_mode"], "zh")
        self.assertEqual(body["model_key"], "bge-base-zh")

    def test_get_reflects_persisted_blob(self) -> None:
        self._json_path.write_text(json.dumps({
            "embedding_language_mode": "en",
            "embedding_model_key": "bge-m3",
        }), encoding="utf-8")
        res = self.client.get("/api/settings/embedding-language-mode")
        body = res.json()
        self.assertEqual(body["language_mode"], "en")
        self.assertEqual(body["model_key"], "bge-m3")

    def test_corrupted_settings_falls_back_to_default(self) -> None:
        self._json_path.write_text("{not json", encoding="utf-8")
        res = self.client.get("/api/settings/embedding-language-mode")
        body = res.json()
        # _read_embedding_settings_blob swallows JSONDecodeError → {} →
        # the language helper returns the zh default.
        self.assertEqual(body["language_mode"], "zh")


class TestPersistenceAcrossServiceReload(unittest.TestCase, _IsolatedSettingsMixin):
    """Spec § 3: the persisted model_key must drive the next process
    startup. We simulate restart with ``_reset_for_tests`` + a fresh
    ``EmbeddingService.get()`` call."""

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
        self._json_path = self._isolate_settings()
        self._provider_patch = _patch_provider()
        self._provider_patch.start()
        self.client = _build_client()

    def tearDown(self) -> None:
        self._provider_patch.stop()
        for p in self._hw_patches:
            p.stop()
        self._release_settings()
        EmbeddingService._reset_for_tests()
        hw.reset_cache()

    def test_next_startup_picks_up_persisted_language(self) -> None:
        # Toggle to en, then "restart" by resetting the singleton.
        self.client.post(
            "/api/settings/embedding-language-mode",
            json={"language_mode": "en"},
        )
        EmbeddingService._reset_for_tests()
        svc = EmbeddingService.get()
        self.assertEqual(svc.get_current_model().model_key, "bge-m3")

    def test_unknown_persisted_key_falls_back_to_language_default(self) -> None:
        """If ``data/settings.json`` references an unregistered model
        (e.g. spec drift), the service falls back to the language
        default rather than crashing."""
        self._json_path.write_text(json.dumps({
            "embedding_language_mode": "zh",
            "embedding_model_key": "no-such-model",
        }), encoding="utf-8")
        EmbeddingService._reset_for_tests()
        svc = EmbeddingService.get()
        self.assertEqual(svc.get_current_model().model_key, "bge-base-zh")


if __name__ == "__main__":
    unittest.main()
