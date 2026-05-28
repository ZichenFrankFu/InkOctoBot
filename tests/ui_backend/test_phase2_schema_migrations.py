"""Tests for Phase 2 schema migrations — embedding_model_key on 5 tables."""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from knowledge.reference_db import ReferenceDB
from storage.idea_schema import ensure_idea_tables
from storage.project_schema import ensure_creation_tables


def _fresh_main_db() -> str:
    db = os.path.join(tempfile.mkdtemp(), "p2.db")
    con = sqlite3.connect(db)
    ensure_creation_tables(con)
    con.execute("INSERT INTO projects (project_id, title) VALUES ('p1','t')")
    con.commit()
    con.close()
    return db


def _cols(db: str, table: str) -> set[str]:
    with sqlite3.connect(db) as c:
        return {r[1] for r in c.execute(f"PRAGMA table_info({table})")}


class TestNewColumns(unittest.TestCase):

    def test_worldbook_has_model_key(self) -> None:
        db = _fresh_main_db()
        self.assertIn("embedding_model_key", _cols(db, "worldbook_entries"))

    def test_skill_index_has_model_key(self) -> None:
        db = _fresh_main_db()
        self.assertIn("embedding_model_key", _cols(db, "skill_index"))

    def test_subplot_threads_has_model_key(self) -> None:
        db = _fresh_main_db()
        self.assertIn("embedding_model_key", _cols(db, "subplot_threads"))

    def test_inspirations_has_model_key(self) -> None:
        db = os.path.join(tempfile.mkdtemp(), "idea.db")
        con = sqlite3.connect(db)
        ensure_idea_tables(con)
        con.close()
        self.assertIn("embedding_model_key", _cols(db, "inspirations"))

    def test_reference_chapters_has_model_key(self) -> None:
        db = os.path.join(tempfile.mkdtemp(), "ref.db")
        ReferenceDB(db)
        self.assertIn("embedding_model_key", _cols(db, "reference_chapters"))


class TestAlterIdempotent(unittest.TestCase):
    """V2 DB without embedding_model_key should ALTER without error."""

    def test_subplot_threads_alter(self) -> None:
        """A v2-style subplot_threads (missing embedding_model_key) gets
        the column added on next ensure_truth_tables call, preserving
        existing rows."""
        db = _fresh_main_db()
        with sqlite3.connect(db) as c:
            try:
                c.execute("ALTER TABLE subplot_threads DROP COLUMN embedding_model_key")
            except sqlite3.OperationalError:
                self.skipTest("SQLite too old for DROP COLUMN simulation")
            cols_before = {r[1] for r in c.execute("PRAGMA table_info(subplot_threads)")}
            self.assertNotIn("embedding_model_key", cols_before)
            c.execute(
                "INSERT INTO subplot_threads (thread_id, project_id, name, "
                "start_chapter, embedding_json, status) VALUES "
                "('s_legacy','p1','旧支线',3,'[0.1, 0.2]', 'setup')"
            )
            c.commit()
            # Re-run migration — should ALTER add the column back.
            from storage.truth_schema import ensure_truth_tables
            ensure_truth_tables(c)
            cols_after = {r[1] for r in c.execute("PRAGMA table_info(subplot_threads)")}
            row = c.execute(
                "SELECT embedding_model_key, embedding_json FROM subplot_threads "
                "WHERE thread_id='s_legacy'"
            ).fetchone()
        self.assertIn("embedding_model_key", cols_after)
        self.assertEqual(row[0], "")
        self.assertEqual(row[1], "[0.1, 0.2]")


class TestPersistRoundtrip(unittest.TestCase):

    def test_worldbook_persist_writes_model_key(self) -> None:
        db = _fresh_main_db()
        from ui.backend.app.services import project_store
        saved = project_store.upsert_worldbook(db, {
            "project_id": "p1", "title": "幽冥谷", "category": "地点",
            "content": "禁地",
        })
        project_store.set_worldbook_embedding(
            db, saved["id"], [0.1, 0.2, 0.3], "hash1", model_key="bge-base-zh",
        )
        got = project_store.get_worldbook(db, saved["id"], include_embedding=True)
        self.assertEqual(got["embedding_model_key"], "bge-base-zh")

    def test_skill_index_persist_writes_model_key(self) -> None:
        db = _fresh_main_db()
        from ui.backend.app.services import skill_index
        skill_index.upsert_skill(db, {
            "skill_id": "k", "display_name": "K", "description": "x",
        })
        skill_index.set_embedding(db, "k", [0.1, 0.2], "hash1",
                                    model_key="bge-large-zh")
        got = skill_index.get_skill(db, "k", include_embedding=True)
        self.assertEqual(got["embedding_model_key"], "bge-large-zh")


if __name__ == "__main__":
    unittest.main()
