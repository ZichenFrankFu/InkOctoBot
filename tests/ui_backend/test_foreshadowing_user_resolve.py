"""Tests for the user-driven "fully resolve" override on pending_hooks.

LOADER_SPEC Loader 11 — user can authoritatively close a hook so the
loader and the auto-detector both stop surfacing it.

Covers:
- new pending_hooks columns survive schema migration
- ``project_store.fully_resolve_hook`` sets the override + flips status
- the foreshadowing loader filters out user-resolved hooks
- ``POST /data/foreshadowing/{hook_id}/fully-resolve`` round-trips
"""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from storage.project_schema import ensure_creation_tables
from ui.backend.app.services import project_store
from ui.backend.app.services.prompt_context.loaders import foreshadowing as fs_loader


def _fresh_db_with_hooks() -> tuple[str, str, str]:
    db = os.path.join(tempfile.mkdtemp(), "fs.db")
    con = sqlite3.connect(db)
    con.execute("PRAGMA foreign_keys=ON")
    ensure_creation_tables(con)
    con.execute("INSERT INTO projects (project_id, title) VALUES ('p1','test')")
    con.execute(
        "INSERT INTO pending_hooks (hook_id, project_id, description, "
        "status, importance, origin_chapter) "
        "VALUES ('h_open', 'p1', '神秘老者', 'open', 'A', 3)"
    )
    con.execute(
        "INSERT INTO pending_hooks (hook_id, project_id, description, "
        "status, importance, origin_chapter) "
        "VALUES ('h_other', 'p1', '玉佩之谜', 'progressing', 'A', 5)"
    )
    con.commit()
    con.close()
    return db, "h_open", "h_other"


class TestSchemaMigration(unittest.TestCase):

    def test_new_columns_present(self) -> None:
        """ALTER TABLE adds the 3 user-resolve columns idempotently."""
        db = os.path.join(tempfile.mkdtemp(), "m.db")
        con = sqlite3.connect(db)
        ensure_creation_tables(con)
        cols = {r[1] for r in con.execute("PRAGMA table_info(pending_hooks)")}
        self.assertIn("user_marked_fully_resolved", cols)
        self.assertIn("user_resolved_at_chapter", cols)
        self.assertIn("user_resolve_notes", cols)
        # second call should not raise
        ensure_creation_tables(con)
        con.close()


class TestFullyResolveHookStore(unittest.TestCase):

    def test_sets_flag_and_status(self) -> None:
        db, hook_id, _ = _fresh_db_with_hooks()
        out = project_store.fully_resolve_hook(
            db, hook_id, chapter_num=12, notes="收回到玉佩谜底",
        )
        self.assertEqual(out["status"], "resolved")
        self.assertEqual(out["user_marked_fully_resolved"], 1)
        self.assertEqual(out["user_resolved_at_chapter"], 12)
        self.assertIn("玉佩", out["user_resolve_notes"])

    def test_unknown_hook_returns_empty(self) -> None:
        db, _, _ = _fresh_db_with_hooks()
        self.assertEqual(project_store.fully_resolve_hook(db, "nope"), {})

    def test_idempotent_second_call_updates_metadata(self) -> None:
        db, hook_id, _ = _fresh_db_with_hooks()
        project_store.fully_resolve_hook(db, hook_id, chapter_num=10)
        out = project_store.fully_resolve_hook(
            db, hook_id, chapter_num=20, notes="补充说明",
        )
        # status stays resolved; bookkeeping refreshed
        self.assertEqual(out["status"], "resolved")
        self.assertEqual(out["user_resolved_at_chapter"], 20)
        self.assertEqual(out["user_resolve_notes"], "补充说明")


class TestLoaderFilter(unittest.TestCase):

    def setUp(self) -> None:
        self.db, self.h_open, self.h_other = _fresh_db_with_hooks()
        self._patcher = mock.patch(
            "ui.backend.app.services.project_paths.get_db_path",
            return_value=self.db,
        )
        self._patcher.start()

    def tearDown(self) -> None:
        self._patcher.stop()

    def test_user_resolved_hook_is_filtered_out(self) -> None:
        # Both hooks visible at first.
        out = fs_loader.load("p1")
        self.assertIn("神秘老者", out)
        self.assertIn("玉佩之谜", out)
        # User authoritatively closes one.
        project_store.fully_resolve_hook(self.db, self.h_open, chapter_num=8)
        out2 = fs_loader.load("p1")
        self.assertNotIn("神秘老者", out2)
        self.assertIn("玉佩之谜", out2)

    def test_user_resolved_survives_status_revert(self) -> None:
        """Even if some downstream flips status back, the user flag wins."""
        project_store.fully_resolve_hook(self.db, self.h_open, chapter_num=8)
        # Simulate auto-detector re-opening the hook (without clearing the flag).
        with sqlite3.connect(self.db) as c:
            c.execute(
                "UPDATE pending_hooks SET status='open' WHERE hook_id=?",
                (self.h_open,),
            )
            c.commit()
        out = fs_loader.load("p1")
        self.assertNotIn("神秘老者", out)


class TestFullyResolveAPI(unittest.TestCase):

    def setUp(self) -> None:
        self.db, self.h_open, _ = _fresh_db_with_hooks()
        self._patcher = mock.patch(
            "ui.backend.app.routers.json_storage_api._db",
            return_value=self.db,
        )
        self._patcher.start()
        # Mount the router in isolation so we don't pull in main.py's
        # numpy/pandas-heavy dependency chain.
        from fastapi import FastAPI
        from ui.backend.app.routers.json_storage_api import router
        app = FastAPI()
        # router already declares prefix="/data"
        app.include_router(router)
        self.client = TestClient(app)

    def tearDown(self) -> None:
        self._patcher.stop()

    def test_endpoint_marks_resolved(self) -> None:
        res = self.client.post(
            f"/data/foreshadowing/{self.h_open}/fully-resolve",
            json={"chapter_num": 15, "notes": "via API"},
        )
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["hook"]["status"], "resolved")
        self.assertEqual(body["hook"]["user_resolved_at_chapter"], 15)

    def test_endpoint_404_on_unknown_id(self) -> None:
        res = self.client.post(
            "/data/foreshadowing/missing_hook_id/fully-resolve",
            json={},
        )
        self.assertEqual(res.status_code, 404)


if __name__ == "__main__":
    unittest.main()
