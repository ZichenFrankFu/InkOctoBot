"""
Unit tests for ui/backend/app/services/project_store.py.

Tests the DB-backed CRUD layer in isolation (no FastAPI).
"""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from storage.project_schema import ensure_creation_tables
from ui.backend.app.services import project_store


def _fresh_db() -> str:
    db = os.path.join(tempfile.mkdtemp(), "store.db")
    con = sqlite3.connect(db)
    con.execute("PRAGMA foreign_keys=ON")
    ensure_creation_tables(con)
    con.execute("INSERT INTO projects (project_id, title) VALUES (?, ?)",
                ("p1", "test"))
    con.commit()
    con.close()
    return db


class TestCharacters(unittest.TestCase):

    def setUp(self) -> None:
        self.db = _fresh_db()

    def test_create_get_update_delete(self) -> None:
        body = {"project_id": "p1", "name": "李星河",
                "role": "主角", "tags": ["主角", "考古"],
                "layer_b": {"mood": "calm"}, "custom_x": 42}
        saved = project_store.upsert_character(self.db, body)
        self.assertTrue(saved["id"].startswith("char_"))
        self.assertEqual(saved["name"], "李星河")
        self.assertEqual(saved["tags"], ["主角", "考古"])
        self.assertEqual(saved["layer_b"], {"mood": "calm"})
        # extra field round-trips
        self.assertEqual(saved["custom_x"], 42)

        fetched = project_store.get_character(self.db, saved["id"])
        self.assertEqual(fetched["name"], "李星河")

        updated = project_store.upsert_character(
            self.db, {**saved, "role": "反派"}
        )
        self.assertEqual(updated["role"], "反派")
        self.assertEqual(updated["id"], saved["id"])

        project_store.delete_character(self.db, saved["id"])
        self.assertIsNone(project_store.get_character(self.db, saved["id"]))

    def test_unique_name_per_project(self) -> None:
        project_store.upsert_character(
            self.db, {"project_id": "p1", "name": "A"}
        )
        with self.assertRaises(ValueError):
            project_store.upsert_character(
                self.db, {"project_id": "p1", "name": "A"}
            )

    def test_list_filters_by_project(self) -> None:
        with sqlite3.connect(self.db) as c:
            c.execute("INSERT INTO projects (project_id, title) VALUES ('p2', 't')")
            c.commit()
        project_store.upsert_character(
            self.db, {"project_id": "p1", "name": "A"}
        )
        project_store.upsert_character(
            self.db, {"project_id": "p2", "name": "B"}
        )
        only_p1 = project_store.list_characters(self.db, "p1")
        self.assertEqual([c["name"] for c in only_p1], ["A"])

    def test_missing_name_raises(self) -> None:
        with self.assertRaises(ValueError):
            project_store.upsert_character(
                self.db, {"project_id": "p1"}
            )


class TestWorldbook(unittest.TestCase):

    def setUp(self) -> None:
        self.db = _fresh_db()

    def test_crud_and_unique(self) -> None:
        saved = project_store.upsert_worldbook(
            self.db, {"project_id": "p1", "title": "星门",
                      "category": "hard_rules", "content": "..."}
        )
        self.assertEqual(saved["category"], "hard_rules")
        self.assertTrue(saved["id"].startswith("wb_"))

        with self.assertRaises(ValueError):
            project_store.upsert_worldbook(
                self.db, {"project_id": "p1", "title": "星门"}
            )

        project_store.delete_worldbook(self.db, saved["id"])
        self.assertIsNone(project_store.get_worldbook(self.db, saved["id"]))


class TestProjectMemory(unittest.TestCase):

    def setUp(self) -> None:
        self.db = _fresh_db()

    def test_add_and_get(self) -> None:
        m1 = project_store.add_project_memory(
            self.db, "p1", "Fact 1", category="rule"
        )
        m2 = project_store.add_project_memory(
            self.db, "p1", "Fact 2", category="setting"
        )
        data = project_store.get_project_memory(self.db, "p1")
        self.assertEqual(len(data["memories"]), 2)
        ids = {m["id"] for m in data["memories"]}
        self.assertIn(m1["id"], ids)
        self.assertIn(m2["id"], ids)

    def test_replace_all(self) -> None:
        project_store.add_project_memory(self.db, "p1", "x")
        project_store.replace_project_memory(
            self.db, "p1", [
                {"id": "mem_a", "content": "y", "category": "note"},
                {"id": "mem_b", "content": "z", "category": "note"},
            ]
        )
        data = project_store.get_project_memory(self.db, "p1")
        self.assertEqual({m["content"] for m in data["memories"]}, {"y", "z"})

    def test_delete_entry(self) -> None:
        m = project_store.add_project_memory(self.db, "p1", "delete me")
        project_store.delete_project_memory_entry(self.db, "p1", m["id"])
        data = project_store.get_project_memory(self.db, "p1")
        self.assertEqual(data["memories"], [])

    def test_empty_content_raises(self) -> None:
        with self.assertRaises(ValueError):
            project_store.add_project_memory(self.db, "p1", "  ")


if __name__ == "__main__":
    unittest.main()
