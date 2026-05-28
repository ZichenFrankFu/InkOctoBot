"""Tests for the batch reindex tool (EMBEDDING_SPEC § 6)."""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import numpy as np

from storage.idea_schema import ensure_idea_tables
from storage.project_schema import ensure_creation_tables
from ui.backend.app.services import project_store, skill_index
from ui.backend.app.services.embedding import (
    EmbeddingService, batch_reindex,
)
from ui.backend.app.services.embedding.providers.base import EmbeddingProvider


class _MockProvider(EmbeddingProvider):
    """Stub provider that returns dimension-correct vectors."""

    def __init__(self, model, device: str = "cpu", *, fp16: bool = False) -> None:
        self._model = model
        self._loaded = False
        self.batch_count = 0

    def load(self) -> None:
        self._loaded = True

    def unload(self) -> None:
        self._loaded = False

    def embed(self, texts, normalize: bool = True):
        self.batch_count += 1
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
    db = os.path.join(tempfile.mkdtemp(), "main.db")
    con = sqlite3.connect(db)
    ensure_creation_tables(con)
    con.execute("INSERT INTO projects (project_id, title) VALUES ('p1','t')")
    con.commit()
    con.close()
    return db


def _seed_stale_rows(db: str) -> dict[str, int]:
    """Seed each of the 5 tables with rows whose embedding_model_key
    is 'old-model' (not 'bge-base-zh', the active default).

    Returns the per-table count of seeded rows.
    """
    counts: dict[str, int] = {}

    # worldbook ×3
    for title in ("幽冥谷", "青云山", "玄阴宗"):
        cat = "组织" if "宗" in title else "地点"
        w = project_store.upsert_worldbook(db, {
            "project_id": "p1", "title": title,
            "category": cat, "content": f"{title}的描述",
        })
        project_store.set_worldbook_embedding(
            db, w["id"], [0.1, 0.2], "oldhash", model_key="old-model",
        )
    counts["worldbook_entries"] = 3

    # skill_index ×2
    for sid in ("sk_a", "sk_b"):
        skill_index.upsert_skill(db, {
            "skill_id": sid, "display_name": sid,
            "description": "test skill",
        })
        skill_index.set_embedding(db, sid, [0.1] * 4, "oldhash",
                                    model_key="old-model")
    counts["skill_index"] = 2

    # subplot_threads ×2 (must be thread_type='sub' to be in scope)
    with sqlite3.connect(db) as c:
        for tid, name in (("sp_a", "支线A"), ("sp_b", "支线B")):
            c.execute(
                "INSERT INTO subplot_threads(thread_id, project_id, name, "
                "description, status, thread_type, start_chapter, "
                "embedding_json, embedding_text_hash, embedding_model_key) "
                "VALUES (?, 'p1', ?, ?, 'setup', 'sub', 1, ?, ?, 'old-model')",
                (tid, name, f"{name} 描述",
                 json.dumps([0.1, 0.2]), "oldhash"),
            )
        c.commit()
    counts["subplot_threads"] = 2

    # inspirations (idea.db sits next to main.db)
    idea_path = os.path.join(os.path.dirname(db), "idea.db")
    icon = sqlite3.connect(idea_path)
    ensure_idea_tables(icon)
    icon.execute(
        "INSERT INTO inspirations(id, project_id, category, title, content, "
        "embedding_json, embedding_text_hash, embedding_model_key) "
        "VALUES ('insp_a', 'p1', 'scene', '老灵感', '内容', '[0.1, 0.2]', "
        "'oldhash', 'old-model')"
    )
    icon.commit()
    icon.close()
    counts["inspirations"] = 1

    # reference_chapters lives in main DB (ReferenceDB picks the same file)
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
            "VALUES ('r1', 1, 'CH1', 'chapter body', '[0.1, 0.2]', "
            "'oldhash', 'old-model')"
        )
        c.commit()
    counts["reference_chapters"] = 1

    return counts


def _patch_db(db_path: str):
    return mock.patch(
        "ui.backend.app.services.project_paths.get_db_path",
        return_value=db_path,
    )


class TestReindex(unittest.TestCase):

    def setUp(self) -> None:
        EmbeddingService._reset_for_tests()
        batch_reindex.reset_for_tests()
        self.db = _fresh_main_db()
        self.expected = _seed_stale_rows(self.db)

    def tearDown(self) -> None:
        EmbeddingService._reset_for_tests()
        batch_reindex.reset_for_tests()

    def _await(self, task_id: str, timeout: float = 5.0) -> dict:
        deadline = time.time() + timeout
        while time.time() < deadline:
            st = batch_reindex.get_status(task_id)
            if st and st["state"] in ("completed", "failed", "cancelled"):
                return st
            time.sleep(0.05)
        self.fail(f"reindex task {task_id} did not finish within {timeout}s")

    def test_inline_run_walks_all_tables(self) -> None:
        with _patch_provider(), _patch_db(self.db):
            task_id = batch_reindex.start_reindex(sync=True, db_path=self.db)
        st = batch_reindex.get_status(task_id)
        self.assertEqual(st["state"], "completed")
        self.assertEqual(st["progress"]["overall_total"], sum(self.expected.values()))
        self.assertEqual(st["progress"]["overall_current"], sum(self.expected.values()))
        # Per-table totals match what we seeded.
        for table, expected_total in self.expected.items():
            self.assertEqual(
                st["progress"]["table_progress"][table]["total"],
                expected_total,
                f"{table} total mismatch",
            )

    def test_all_rows_get_new_model_key(self) -> None:
        with _patch_provider(), _patch_db(self.db):
            batch_reindex.start_reindex(sync=True, db_path=self.db)
        # Every row should now carry the active model_key.
        with sqlite3.connect(self.db) as c:
            for row in c.execute(
                "SELECT embedding_model_key FROM worldbook_entries"
            ):
                self.assertEqual(row[0], "bge-base-zh")
            for row in c.execute("SELECT embedding_model_key FROM skill_index"):
                self.assertEqual(row[0], "bge-base-zh")
            for row in c.execute(
                "SELECT embedding_model_key FROM subplot_threads "
                "WHERE thread_type='sub'"
            ):
                self.assertEqual(row[0], "bge-base-zh")
            for row in c.execute(
                "SELECT embedding_model_key FROM reference_chapters"
            ):
                self.assertEqual(row[0], "bge-base-zh")
        idea_path = os.path.join(os.path.dirname(self.db), "idea.db")
        with sqlite3.connect(idea_path) as c:
            for row in c.execute(
                "SELECT embedding_model_key FROM inspirations"
            ):
                self.assertEqual(row[0], "bge-base-zh")

    def test_async_run_completes(self) -> None:
        with _patch_provider(), _patch_db(self.db):
            task_id = batch_reindex.start_reindex(db_path=self.db)
            st = self._await(task_id)
        self.assertEqual(st["state"], "completed")

    def test_get_status_unknown_task(self) -> None:
        self.assertIsNone(batch_reindex.get_status("rx_nope"))

    def test_cancel_unknown_task(self) -> None:
        self.assertFalse(batch_reindex.cancel("rx_nope"))

    def test_idempotent_second_run_is_noop(self) -> None:
        with _patch_provider(), _patch_db(self.db):
            batch_reindex.start_reindex(sync=True, db_path=self.db)
            # Second run: every row already has the active model_key, so
            # the worker counts 0 stale items per table.
            t2 = batch_reindex.start_reindex(sync=True, db_path=self.db)
        st = batch_reindex.get_status(t2)
        self.assertEqual(st["state"], "completed")
        self.assertEqual(st["progress"]["overall_total"], 0)

    def test_persist_failure_isolated(self) -> None:
        """A persist exception on one row gets logged + skipped, the
        rest of the batch still completes."""
        with _patch_provider(), _patch_db(self.db), \
             mock.patch.object(
                 batch_reindex, "_persist_worldbook",
                 side_effect=RuntimeError("disk full"),
             ):
            task_id = batch_reindex.start_reindex(sync=True, db_path=self.db)
        st = batch_reindex.get_status(task_id)
        # state remains 'completed' — only the worldbook rows errored.
        self.assertEqual(st["state"], "completed")
        wb = st["progress"]["table_progress"]["worldbook_entries"]
        self.assertEqual(wb["errors"], wb["total"])
        self.assertGreaterEqual(st["error_count"], 1)

    def test_target_model_switches_active(self) -> None:
        """``start_reindex('bge-large-zh')`` switches the active service
        if it isn't already pointed there."""
        with _patch_provider(), _patch_db(self.db):
            self.assertEqual(
                EmbeddingService.get().get_current_model().model_key,
                "bge-base-zh",
            )
            batch_reindex.start_reindex(
                target_model_key="bge-large-zh",
                sync=True, db_path=self.db,
            )
            self.assertEqual(
                EmbeddingService.get().get_current_model().model_key,
                "bge-large-zh",
            )


class TestCancel(unittest.TestCase):

    def setUp(self) -> None:
        EmbeddingService._reset_for_tests()
        batch_reindex.reset_for_tests()
        self.db = _fresh_main_db()
        _seed_stale_rows(self.db)

    def tearDown(self) -> None:
        EmbeddingService._reset_for_tests()
        batch_reindex.reset_for_tests()

    def test_cancel_flag_stops_running_task(self) -> None:
        """A cancel mid-run flips state to 'cancelled'.

        We achieve a deterministic mid-run cancel by patching one of
        the persist helpers to flip the cancel flag the first time it
        fires. The next loop iteration sees ``cancel_flag=True`` and
        bails before touching the remaining tables.
        """
        with _patch_provider(), _patch_db(self.db):
            original = batch_reindex._persist_worldbook
            triggered = {"v": False}

            def _persist_and_cancel(db_path, row_id, vec, h, mk):
                original(db_path, row_id, vec, h, mk)
                if not triggered["v"]:
                    triggered["v"] = True
                    task_id = next(iter(batch_reindex._tasks))
                    batch_reindex.cancel(task_id)

            with mock.patch.object(
                batch_reindex, "_persist_worldbook",
                side_effect=_persist_and_cancel,
            ):
                tid = batch_reindex.start_reindex(sync=True, db_path=self.db)
        st = batch_reindex.get_status(tid)
        self.assertEqual(st["state"], "cancelled")
        # At least one row got through before the cancel.
        wb = st["progress"]["table_progress"]["worldbook_entries"]
        self.assertGreater(wb["current"], 0)


if __name__ == "__main__":
    unittest.main()
