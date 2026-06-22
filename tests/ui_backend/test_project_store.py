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

    def test_dynamic_snapshots_field_dropped_silently(self) -> None:
        """LOADER_SPEC v3 Batch 5: dynamic_snapshots no longer lives on
        the character row — the snapshot system owns its own table.
        Any leftover payloads are dropped without raising and never
        surface on read."""
        saved = project_store.upsert_character(
            self.db,
            {"project_id": "p1", "name": "主角",
             "dynamic_snapshots": [{"chapter": "第3章"}]},
        )
        self.assertNotIn("dynamic_snapshots", saved)
        loaded = project_store.get_character(self.db, saved["id"])
        self.assertNotIn("dynamic_snapshots", loaded)


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

    def test_embedding_column_lifecycle(self) -> None:
        """embedding persists; edits to content invalidate it; idle edits don't."""
        saved = project_store.upsert_worldbook(
            self.db, {"project_id": "p1", "title": "星门",
                      "category": "hard_rules", "content": "传送门"}
        )
        # Fresh entry has no embedding yet.
        fetched = project_store.get_worldbook(
            self.db, saved["id"], include_embedding=True,
        )
        self.assertEqual(fetched["embedding"], [])

        # Backfill an embedding the way the loader would.
        text = project_store.worldbook_embedding_text(fetched)
        h = project_store._hash_embedding_text(text)
        project_store.set_worldbook_embedding(
            self.db, saved["id"], [0.1, 0.2, 0.3], h,
        )
        fetched = project_store.get_worldbook(
            self.db, saved["id"], include_embedding=True,
        )
        self.assertEqual(fetched["embedding"], [0.1, 0.2, 0.3])
        self.assertEqual(fetched["embedding_text_hash"], h)

        # No-op upsert (same canonical text) keeps the embedding.
        project_store.upsert_worldbook(
            self.db, {**fetched, "sort_order": 5},
        )
        fetched = project_store.get_worldbook(
            self.db, saved["id"], include_embedding=True,
        )
        self.assertEqual(fetched["embedding"], [0.1, 0.2, 0.3])

        # Editing content invalidates the embedding.
        project_store.upsert_worldbook(
            self.db, {**fetched, "content": "彻底改了"},
        )
        fetched = project_store.get_worldbook(
            self.db, saved["id"], include_embedding=True,
        )
        self.assertEqual(fetched["embedding"], [])
        self.assertEqual(fetched["embedding_text_hash"], "")


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


class TestProjectsPlatformCategory(unittest.TestCase):
    """upsert_project must persist platform + category into the dedicated
    columns so the platform_market loader's project lookup works.

    Regression: previously these two fields fell into the catch-all
    ``style_profile_json`` extra blob, leaving ``projects.platform`` /
    ``projects.category`` empty → loader skipped silently.
    """

    def setUp(self) -> None:
        from storage.project_schema import _ensure_projects_market_columns

        self.db = os.path.join(tempfile.mkdtemp(), "p.db")
        con = sqlite3.connect(self.db)
        con.execute("PRAGMA foreign_keys=ON")
        ensure_creation_tables(con)
        _ensure_projects_market_columns(con)
        con.commit()
        con.close()

    def test_upsert_writes_platform_and_category_columns(self) -> None:
        project_store.upsert_project(self.db, {
            "id": "p1", "name": "test", "platform": "起点",
            "genre": "玄幻", "category": "东方玄幻",
        })
        with sqlite3.connect(self.db) as con:
            con.row_factory = sqlite3.Row
            row = con.execute(
                "SELECT platform, category, genre FROM projects "
                "WHERE project_id = 'p1'"
            ).fetchone()
        self.assertEqual(row["platform"], "起点")
        self.assertEqual(row["category"], "东方玄幻")
        self.assertEqual(row["genre"], "玄幻")

    def test_get_project_returns_platform_and_category(self) -> None:
        project_store.upsert_project(self.db, {
            "id": "p1", "name": "test", "platform": "番茄",
            "category": "都市生活",
        })
        proj = project_store.get_project(self.db, "p1")
        self.assertIsNotNone(proj)
        self.assertEqual(proj["platform"], "番茄")
        self.assertEqual(proj["category"], "都市生活")

    def test_upsert_does_not_double_stash_in_style_profile(self) -> None:
        """``platform`` / ``category`` must NOT land in ``style_profile_json``
        — that's what the regression was."""
        import json
        project_store.upsert_project(self.db, {
            "id": "p1", "name": "test", "platform": "起点",
            "category": "都市", "custom_field": "should-be-in-extra",
        })
        with sqlite3.connect(self.db) as con:
            (raw,) = con.execute(
                "SELECT style_profile_json FROM projects WHERE project_id='p1'"
            ).fetchone()
        extra = json.loads(raw or "{}")
        self.assertNotIn("platform", extra)
        self.assertNotIn("category", extra)
        self.assertEqual(extra.get("custom_field"), "should-be-in-extra")


if __name__ == "__main__":
    unittest.main()
