"""End-to-end acceptance tests for the embedding subsystem (EMBEDDING_SPEC § 13 Phase 5).

These tests tie together the pieces that previous phases unit-tested
in isolation: a row is embedded under model A, the active model
flips to model B (loader now treats the row as ``_cross_model`` and
sinks it from ranking), the user runs reindex, and the loader sees
the row as a first-class match again under model B.

What this guards against:
- A loader stops honoring ``embedding_model_key`` (silently mixes
  vectors of different dimension).
- ``batch_reindex`` updates one table but forgets another.
- A switch path mutates the registry but the loader's
  ``current_embedding_model_key()`` doesn't agree.
- The language toggle path (zh ↔ en) leaves the service in an
  inconsistent state after reindex.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import numpy as np
from fastapi import FastAPI
from fastapi.testclient import TestClient

from storage.idea_schema import ensure_idea_tables
from storage.project_schema import ensure_creation_tables
from ui.backend.app import settings as settings_module
from ui.backend.app.services import project_store, skill_index
from ui.backend.app.services.embedding import (
    EmbeddingService, batch_reindex, hardware_detector as hw,
)
from ui.backend.app.services.embedding.providers.base import EmbeddingProvider
from ui.backend.app.services.prompt_context.loaders import worldbook as wb_loader


class _MockProvider(EmbeddingProvider):
    """Returns unit vectors at the current model's dimension."""

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


def _fresh_main_db() -> str:
    db = os.path.join(tempfile.mkdtemp(), "lifecycle.db")
    con = sqlite3.connect(db)
    ensure_creation_tables(con)
    con.execute("INSERT INTO projects (project_id, title) VALUES ('p1','t')")
    con.commit()
    con.close()
    return db


def _seed_corpus(db: str, model_key: str) -> None:
    """Seed one row in each of the 4 SQL tables + idea.db, all marked
    with ``model_key``. Use this to put the corpus into a known state
    matching some active model."""
    # worldbook
    w = project_store.upsert_worldbook(db, {
        "project_id": "p1", "title": "幽冥谷",
        "category": "地点", "content": "千年禁地",
    })
    project_store.set_worldbook_embedding(
        db, w["id"], [0.5, 0.5], "h_wb", model_key=model_key,
    )

    # skill_index
    skill_index.upsert_skill(db, {
        "skill_id": "sk1", "display_name": "御剑术", "description": "御剑",
    })
    skill_index.set_embedding(db, "sk1", [0.5, 0.5], "h_sk", model_key=model_key)

    # subplot_threads (must be 'sub' to be reindex-eligible)
    with sqlite3.connect(db) as c:
        c.execute(
            "INSERT INTO subplot_threads(thread_id, project_id, name, "
            "description, status, thread_type, start_chapter, "
            "embedding_json, embedding_text_hash, embedding_model_key) "
            "VALUES ('sp1','p1','支线A','desc','setup','sub',1,?,?,?)",
            (json.dumps([0.5, 0.5]), "h_sp", model_key),
        )
        c.commit()

    # inspirations (idea.db sits next to main.db)
    idea_path = os.path.join(os.path.dirname(db), "idea.db")
    icon = sqlite3.connect(idea_path)
    ensure_idea_tables(icon)
    icon.execute(
        "INSERT INTO inspirations(id, project_id, category, title, content, "
        "embedding_json, embedding_text_hash, embedding_model_key) "
        "VALUES ('insp1','p1','scene','灵感','内容','[0.5, 0.5]','h_in', ?)",
        (model_key,),
    )
    icon.commit()
    icon.close()

    # reference_chapters
    from knowledge.reference_db import ReferenceDB
    ReferenceDB(db)
    with sqlite3.connect(db) as c:
        c.execute(
            "INSERT INTO reference_works (ref_id, title, media_type, source) "
            "VALUES ('r1', 'X', 'web_novel', 'manual')"
        )
        c.execute(
            "INSERT INTO reference_chapters (ref_id, number, title, content, "
            "embedding_json, embedding_text_hash, embedding_model_key) "
            "VALUES ('r1', 1, 'CH1', 'body', '[0.5, 0.5]', 'h_rc', ?)",
            (model_key,),
        )
        c.commit()


def _read_model_keys(db: str) -> dict[str, set[str]]:
    """Snapshot every persisted embedding_model_key across the 5 tables.
    Sets (not lists) so tests can assert "every row equals X"."""
    out: dict[str, set[str]] = {}
    with sqlite3.connect(db) as c:
        out["worldbook_entries"] = {
            r[0] for r in c.execute(
                "SELECT embedding_model_key FROM worldbook_entries"
            )
        }
        out["skill_index"] = {
            r[0] for r in c.execute(
                "SELECT embedding_model_key FROM skill_index"
            )
        }
        out["subplot_threads"] = {
            r[0] for r in c.execute(
                "SELECT embedding_model_key FROM subplot_threads "
                "WHERE thread_type='sub'"
            )
        }
        out["reference_chapters"] = {
            r[0] for r in c.execute(
                "SELECT embedding_model_key FROM reference_chapters"
            )
        }
    idea_path = os.path.join(os.path.dirname(db), "idea.db")
    with sqlite3.connect(idea_path) as c:
        out["inspirations"] = {
            r[0] for r in c.execute(
                "SELECT embedding_model_key FROM inspirations"
            )
        }
    return out


def _patch_db(db_path: str):
    return mock.patch(
        "ui.backend.app.services.project_paths.get_db_path",
        return_value=db_path,
    )


def _isolate_settings_json() -> tuple[mock._patch, Path]:
    tmp = Path(tempfile.mkdtemp()) / "settings.json"
    p = mock.patch.object(
        settings_module, "_embedding_settings_path", return_value=tmp,
    )
    return p, tmp


class TestSwitchReindexCycle(unittest.TestCase):
    """The canonical user flow: switch active model → run reindex →
    every row's ``embedding_model_key`` is in lockstep with the
    service. Then switch back and reindex again — proves the cycle
    is symmetric, not a one-way drift."""

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
        self._settings_patch, _ = _isolate_settings_json()
        self._settings_patch.start()
        self.db = _fresh_main_db()
        # Seed under bge-base-zh (the boot default).
        _seed_corpus(self.db, model_key="bge-base-zh")

    def tearDown(self) -> None:
        self._settings_patch.stop()
        for p in self._hw_patches:
            p.stop()
        EmbeddingService._reset_for_tests()
        batch_reindex.reset_for_tests()
        hw.reset_cache()

    def test_switch_then_reindex_aligns_all_tables(self) -> None:
        """After switch + reindex, every row in every table reports
        the new model_key — no table left behind."""
        with _patch_provider(), _patch_db(self.db):
            # Active is bge-base-zh → corpus matches.
            self.assertEqual(
                _read_model_keys(self.db)["worldbook_entries"],
                {"bge-base-zh"},
            )
            # Switch to bge-large-zh. Persisted rows still on bge-base-zh.
            EmbeddingService.get().switch_model("bge-large-zh")
            keys_pre = _read_model_keys(self.db)
            for table, vals in keys_pre.items():
                self.assertEqual(vals, {"bge-base-zh"}, f"{table} pre-reindex")
            # Run reindex synchronously.
            batch_reindex.start_reindex(sync=True, db_path=self.db)
            keys_post = _read_model_keys(self.db)
            for table, vals in keys_post.items():
                self.assertEqual(vals, {"bge-large-zh"}, f"{table} post-reindex")

    def test_back_and_forth_cycle(self) -> None:
        """Switching back to the original model + reindex returns the
        corpus to the original state — proves the operation is
        symmetric, no one-way data loss."""
        with _patch_provider(), _patch_db(self.db):
            EmbeddingService.get().switch_model("bge-large-zh")
            batch_reindex.start_reindex(sync=True, db_path=self.db)
            EmbeddingService.get().switch_model("bge-base-zh")
            batch_reindex.start_reindex(sync=True, db_path=self.db)
            for table, vals in _read_model_keys(self.db).items():
                self.assertEqual(vals, {"bge-base-zh"}, table)


class TestLoaderRecoversAcrossReindex(unittest.TestCase):
    """Spec § 7.2 + § 6 together: a cross-model row sinks from
    ranking after the switch, then comes back as a first-class match
    after reindex. This is the user-visible 'do my searches work
    after I change models?' invariant."""

    def setUp(self) -> None:
        EmbeddingService._reset_for_tests()
        batch_reindex.reset_for_tests()
        self.db = _fresh_main_db()
        # Seed a worldbook entry under bge-base-zh.
        _seed_corpus(self.db, model_key="bge-base-zh")
        # Add a chapter synopsis so the worldbook loader has a query.
        project_store.save_editor_doc(self.db, "p1", {
            "volumes": [{"chapters": [{
                "id": "ch1", "chapter_id": "ch1",
                "synopsis": "主角进入幽冥谷",
            }]}],
        })

    def tearDown(self) -> None:
        EmbeddingService._reset_for_tests()
        batch_reindex.reset_for_tests()

    def test_cross_model_row_returns_after_reindex(self) -> None:
        with _patch_provider(), _patch_db(self.db):
            # Step 1: switch to bge-large-zh. The seeded row's key is
            # now stale (bge-base-zh ≠ bge-large-zh).
            EmbeddingService.get().switch_model("bge-large-zh")
            with mock.patch.object(
                wb_loader, "embed_sync",
                return_value=[[1.0, 0.0]],   # only for the query
            ):
                ordered_after_switch = wb_loader._prepare("p1", "ch1")
            self.assertIsNotNone(ordered_after_switch)
            # The stale row is still in the list (we never delete data)
            # but tagged as cross-model so it sinks below fresh rows.
            wb_row = next(r for r in ordered_after_switch if r["title"] == "幽冥谷")
            self.assertTrue(wb_row.get("_cross_model"))

            # Step 2: reindex.
            batch_reindex.start_reindex(sync=True, db_path=self.db)

            # Step 3: row now matches the active model again — loader
            # should NOT mark it cross-model on the next query.
            with mock.patch.object(
                wb_loader, "embed_sync",
                return_value=[[1.0, 0.0], [1.0, 0.0]],  # query + row recompute
            ):
                ordered_after_reindex = wb_loader._prepare("p1", "ch1")
            wb_row_after = next(
                r for r in ordered_after_reindex if r["title"] == "幽冥谷"
            )
            self.assertFalse(wb_row_after.get("_cross_model"))


class TestLanguageToggleEndToEnd(unittest.TestCase):
    """Spec § 3: language toggle force-switches model_key + the user
    can then reindex to align the corpus. This test walks the full
    POST /api/settings/embedding-language-mode → reindex flow."""

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
        self._settings_patch, _ = _isolate_settings_json()
        self._settings_patch.start()
        self.db = _fresh_main_db()
        _seed_corpus(self.db, model_key="bge-base-zh")
        # Build a TestClient with BOTH routers.
        from ui.backend.app.routers.embedding_api import router, settings_router
        app = FastAPI()
        app.include_router(router)
        app.include_router(settings_router)
        self._provider_patch = _patch_provider()
        self._provider_patch.start()
        self.client = TestClient(app)

    def tearDown(self) -> None:
        self._provider_patch.stop()
        self._settings_patch.stop()
        for p in self._hw_patches:
            p.stop()
        EmbeddingService._reset_for_tests()
        batch_reindex.reset_for_tests()
        hw.reset_cache()

    def test_zh_to_en_then_reindex_aligns_corpus(self) -> None:
        """POST language-mode=en flips the active model to bge-m3.
        Persisted rows are now stale. After reindex, every row is on
        bge-m3 and the service agrees."""
        with _patch_db(self.db):
            res = self.client.post(
                "/api/settings/embedding-language-mode",
                json={"language_mode": "en"},
            )
            self.assertEqual(res.status_code, 200)
            self.assertEqual(res.json()["model_key"], "bge-m3")
            self.assertTrue(res.json()["switch_result"]["need_reindex"])
            # Corpus is still on bge-base-zh — confirms switch ≠ reindex.
            self.assertEqual(
                _read_model_keys(self.db)["worldbook_entries"],
                {"bge-base-zh"},
            )
            # Run reindex against the now-active model.
            batch_reindex.start_reindex(sync=True, db_path=self.db)
            # Corpus realigned.
            for table, vals in _read_model_keys(self.db).items():
                self.assertEqual(vals, {"bge-m3"}, table)
            # Service still reports bge-m3.
            self.assertEqual(
                EmbeddingService.get().get_current_model().model_key, "bge-m3",
            )


if __name__ == "__main__":
    unittest.main()
