"""Tests for chapter_summaries.is_anchor column + toggle helper."""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from storage.project_schema import ensure_creation_tables
from ui.backend.app.services import project_store


def _fresh_db() -> str:
    db = os.path.join(tempfile.mkdtemp(), "an.db")
    con = sqlite3.connect(db)
    con.execute("PRAGMA foreign_keys=ON")
    ensure_creation_tables(con)
    con.execute("INSERT INTO projects (project_id, title) VALUES ('p1','t')")
    con.commit()
    con.close()
    return db


def _seed_summary(db: str, chapter: int, *, title: str = "", text: str = "") -> None:
    with sqlite3.connect(db) as c:
        c.execute(
            "INSERT INTO chapter_summaries(summary_id, project_id, chapter_num, "
            "summary_text, title) VALUES (?, 'p1', ?, ?, ?)",
            (f"sum_{chapter}", chapter, text or f"ch {chapter}", title),
        )
        c.commit()


class TestSchema(unittest.TestCase):

    def test_columns_present_on_fresh_db(self) -> None:
        db = _fresh_db()
        with sqlite3.connect(db) as c:
            cols = {r[1] for r in c.execute("PRAGMA table_info(chapter_summaries)")}
        for col in ("is_anchor", "title"):
            self.assertIn(col, cols)

    def test_alter_idempotent(self) -> None:
        db = _fresh_db()
        with sqlite3.connect(db) as c:
            ensure_creation_tables(c)
            cols = {r[1] for r in c.execute("PRAGMA table_info(chapter_summaries)")}
        self.assertIn("is_anchor", cols)

    def test_v1_db_upgrade_preserves_rows(self) -> None:
        """A pre-v3 chapter_summaries (no is_anchor/title) upgrades safely."""
        db = os.path.join(tempfile.mkdtemp(), "v1.db")
        con = sqlite3.connect(db)
        con.execute("""
            CREATE TABLE projects (project_id TEXT PRIMARY KEY, title TEXT)
        """)
        con.execute("INSERT INTO projects VALUES ('p1','t')")
        con.execute("""
            CREATE TABLE chapter_summaries (
                summary_id TEXT PRIMARY KEY,
                project_id TEXT,
                chapter_num INTEGER,
                summary_text TEXT,
                is_active INTEGER DEFAULT 1
            )
        """)
        con.execute(
            "INSERT INTO chapter_summaries VALUES "
            "('legacy','p1', 1, '旧摘要', 1)"
        )
        con.commit()
        ensure_creation_tables(con)
        cols = {r[1] for r in con.execute("PRAGMA table_info(chapter_summaries)")}
        row = con.execute(
            "SELECT summary_text, is_anchor, title FROM chapter_summaries "
            "WHERE summary_id = 'legacy'"
        ).fetchone()
        con.close()
        self.assertIn("is_anchor", cols)
        self.assertIn("title", cols)
        self.assertEqual(row[0], "旧摘要")
        self.assertEqual(row[1], 0)
        self.assertEqual(row[2], "")


class TestToggleHelper(unittest.TestCase):

    def test_toggle_flips_state(self) -> None:
        db = _fresh_db()
        _seed_summary(db, 3)
        first = project_store.toggle_chapter_summary_anchor(db, "p1", 3)
        self.assertEqual(first["is_anchor"], 1)
        second = project_store.toggle_chapter_summary_anchor(db, "p1", 3)
        self.assertEqual(second["is_anchor"], 0)

    def test_toggle_missing_summary_returns_none(self) -> None:
        db = _fresh_db()
        self.assertIsNone(
            project_store.toggle_chapter_summary_anchor(db, "p1", 99),
        )

    def test_set_explicit_state(self) -> None:
        db = _fresh_db()
        _seed_summary(db, 5)
        out = project_store.set_chapter_summary_anchor(db, "p1", 5, True)
        self.assertEqual(out["is_anchor"], 1)
        out2 = project_store.set_chapter_summary_anchor(db, "p1", 5, True)  # idempotent
        self.assertEqual(out2["is_anchor"], 1)
        out3 = project_store.set_chapter_summary_anchor(db, "p1", 5, False)
        self.assertEqual(out3["is_anchor"], 0)


class TestAnchorAPI(unittest.TestCase):

    def setUp(self) -> None:
        self.db = _fresh_db()
        _seed_summary(self.db, 5, text="第5章总结")
        self._patcher = mock.patch(
            "ui.backend.app.routers.json_storage_api._db", return_value=self.db,
        )
        self._patcher.start()
        from ui.backend.app.routers.json_storage_api import router
        app = FastAPI()
        app.include_router(router)
        self.client = TestClient(app)

    def tearDown(self) -> None:
        self._patcher.stop()

    def test_endpoint_toggles(self) -> None:
        res = self.client.post(
            "/data/chapters/5/toggle-anchor",
            json={"project_id": "p1"},
        )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["is_anchor"], 1)
        res2 = self.client.post(
            "/data/chapters/5/toggle-anchor",
            json={"project_id": "p1"},
        )
        self.assertEqual(res2.json()["is_anchor"], 0)

    def test_missing_project_id_400(self) -> None:
        res = self.client.post("/data/chapters/5/toggle-anchor", json={})
        self.assertEqual(res.status_code, 400)

    def test_unknown_chapter_404(self) -> None:
        res = self.client.post(
            "/data/chapters/99/toggle-anchor",
            json={"project_id": "p1"},
        )
        self.assertEqual(res.status_code, 404)


if __name__ == "__main__":
    unittest.main()
