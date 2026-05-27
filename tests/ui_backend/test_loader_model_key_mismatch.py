"""Tests that loaders skip rows whose embedding_model_key doesn't match
the active EmbeddingService.

Spec § 7.2: cross-model rows are skipped from cosine ranking + a
debug warning logged. Stale-by-text-hash is still eligible for lazy
recompute (existing behavior).
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from storage.project_schema import ensure_creation_tables
from ui.backend.app.services import project_store
from ui.backend.app.services.embedding import EmbeddingService
from ui.backend.app.services.prompt_context.loaders import worldbook as wb_loader


def _fresh_db() -> str:
    db = os.path.join(tempfile.mkdtemp(), "mm.db")
    con = sqlite3.connect(db)
    ensure_creation_tables(con)
    con.execute("INSERT INTO projects (project_id, title) VALUES ('p1','t')")
    con.commit()
    con.close()
    return db


def _seed_worldbook_with_old_embedding(db: str) -> str:
    # Two entries: one with the legacy model_key, one fresh (no embedding)
    saved_old = project_store.upsert_worldbook(db, {
        "project_id": "p1", "title": "幽冥谷",
        "category": "地点", "content": "千年禁地",
    })
    text = project_store.worldbook_embedding_text(saved_old)
    h = project_store._hash_embedding_text(text)
    project_store.set_worldbook_embedding(
        db, saved_old["id"], [0.99, 0.01], h, model_key="text2vec-zh",  # legacy
    )
    # Fresh entry, no embedding yet
    project_store.upsert_worldbook(db, {
        "project_id": "p1", "title": "青云山",
        "category": "地点", "content": "新地点",
    })
    return saved_old["id"]


class _Env:
    def __init__(self, db: str) -> None:
        self._patches = [
            mock.patch(
                "ui.backend.app.services.project_paths.get_db_path",
                return_value=db,
            ),
            # ChromaDB / embedding backend not loaded in test env — the
            # service falls back via EmbeddingService.
        ]

    def __enter__(self):
        for p in self._patches:
            p.start()
        return self

    def __exit__(self, *exc):
        for p in self._patches:
            p.stop()


class TestCrossModelSkip(unittest.TestCase):

    def setUp(self) -> None:
        EmbeddingService._reset_for_tests()
        self.db = _fresh_db()
        self.old_id = _seed_worldbook_with_old_embedding(self.db)
        # Seed a chapter so the worldbook loader has a synopsis.
        project_store.save_editor_doc(self.db, "p1", {
            "volumes": [{"chapters": [{
                "id": "ch1", "chapter_id": "ch1",
                "synopsis": "主角进入幽冥谷",
            }]}],
        })

    def tearDown(self) -> None:
        EmbeddingService._reset_for_tests()

    def test_cross_model_row_excluded_from_ranking(self) -> None:
        """With service on bge-base-zh, the row with model_key=text2vec-zh
        should be tagged ``_cross_model`` and sink to the bottom of
        the ranked list."""
        # Mock embed_sync to return query vec + one fresh embedding for
        # 青云山 (the not-yet-embedded row). The cross-model 幽冥谷
        # never enters the embed_sync batch since the loader marks it
        # ``_cross_model`` and skips re-embedding.
        with _Env(self.db), \
             mock.patch.object(
                 wb_loader, "embed_sync",
                 return_value=[[1.0, 0.0], [1.0, 0.0]],
             ):
            ordered = wb_loader._prepare("p1", "ch1")
        # The cross-model row should be present (so fallback render
        # still includes it) but ranked last.
        self.assertIsNotNone(ordered)
        names = [r["title"] for r in ordered]
        # 幽冥谷 (cross-model, score -inf) should appear AFTER 青云山
        # (which now has a vec matching the query → score 1.0)
        # because the loader sinks cross-model rows to the bottom.
        # still goes last).
        self.assertEqual(names[-1], "幽冥谷")


class TestCurrentModelKeyHelper(unittest.TestCase):

    def setUp(self) -> None:
        EmbeddingService._reset_for_tests()

    def test_returns_active_model_key(self) -> None:
        from ui.backend.app.services.prompt_context.utils import (
            current_embedding_model_key,
        )
        self.assertEqual(current_embedding_model_key(), "bge-base-zh")

    def test_returns_empty_when_service_unavailable(self) -> None:
        from ui.backend.app.services.prompt_context.utils import (
            current_embedding_model_key,
        )
        with mock.patch(
            "ui.backend.app.services.embedding.get_embedding_service",
            side_effect=RuntimeError("not available"),
        ):
            self.assertEqual(current_embedding_model_key(), "")


class TestEmbedSyncShape(unittest.TestCase):
    """embed_sync still returns list[list[float]] via EmbeddingService."""

    def setUp(self) -> None:
        EmbeddingService._reset_for_tests()

    def test_back_compat_shape(self) -> None:
        from ui.backend.app.services.prompt_context.utils import embed_sync
        with mock.patch.object(
            EmbeddingService, "embed_batch",
            return_value=[b"\x00" * 768 * 4, b"\x00" * 768 * 4],
        ):
            out = embed_sync(["a", "b"])
        self.assertEqual(len(out), 2)
        self.assertEqual(len(out[0]), 768)
        self.assertEqual(out[0][0], 0.0)

    def test_empty_input(self) -> None:
        from ui.backend.app.services.prompt_context.utils import embed_sync
        self.assertEqual(embed_sync([]), [])


if __name__ == "__main__":
    unittest.main()
