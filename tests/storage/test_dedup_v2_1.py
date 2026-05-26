"""
v2.1 MEMORY_VS_TRUTH dedup tests.

Verifies:
  - episodic_events.causality_json was dropped
  - worldbook_entries.category rejects 角色-shaped values via DB CHECK
  - project_store.upsert_worldbook coerces banned categories to '杂项'
  - v1 DBs with causality_json get the column dropped on first
    ensure_creation_tables call
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
    db = os.path.join(tempfile.mkdtemp(), "dedup.db")
    con = sqlite3.connect(db)
    con.execute("PRAGMA foreign_keys=ON")
    ensure_creation_tables(con)
    con.execute("INSERT INTO projects (project_id, title) VALUES ('p1', 't')")
    con.commit()
    con.close()
    return db


class TestCausalityColumnDropped(unittest.TestCase):

    def test_fresh_db_has_no_causality_column(self) -> None:
        db = _fresh_db()
        with sqlite3.connect(db) as c:
            cols = {r[1] for r in c.execute("PRAGMA table_info(episodic_events)")}
        self.assertNotIn("causality_json", cols)

    def test_v1_db_with_causality_gets_column_dropped(self) -> None:
        """Existing v1 DB with causality_json → column removed on upgrade."""
        db = os.path.join(tempfile.mkdtemp(), "v1.db")
        with sqlite3.connect(db) as c:
            c.execute("CREATE TABLE projects (project_id TEXT PRIMARY KEY)")
            c.execute(
                """CREATE TABLE episodic_events (
                       event_id TEXT PRIMARY KEY,
                       project_id TEXT NOT NULL,
                       chapter_num INTEGER NOT NULL,
                       event_type TEXT,
                       description TEXT,
                       characters_json TEXT,
                       causality_json TEXT NOT NULL DEFAULT '{}',
                       importance INTEGER)"""
            )
            c.commit()
        with sqlite3.connect(db) as c:
            ensure_creation_tables(c)
            cols = {r[1] for r in c.execute("PRAGMA table_info(episodic_events)")}
        self.assertNotIn("causality_json", cols)


class TestWorldbookCategoryConstraint(unittest.TestCase):

    def setUp(self) -> None:
        self.db = _fresh_db()

    def test_banned_category_raw_insert_rejected_by_check(self) -> None:
        """Raw SQL with banned category → IntegrityError via CHECK."""
        with sqlite3.connect(self.db) as c:
            c.execute("PRAGMA foreign_keys=ON")
            with self.assertRaises(sqlite3.IntegrityError):
                c.execute(
                    "INSERT INTO worldbook_entries (entry_id, project_id, "
                    "title, category, content) VALUES (?, ?, ?, ?, ?)",
                    ("wb_x", "p1", "李星河", "角色", "试图把角色放进世界书"),
                )

    def test_banned_category_coerced_via_project_store(self) -> None:
        """project_store layer coerces '角色' to '杂项' (friendly fallback)."""
        saved = project_store.upsert_worldbook(self.db, {
            "project_id": "p1", "title": "李星河", "category": "角色",
            "content": "试图把角色放进世界书",
        })
        self.assertEqual(saved["category"], "杂项")

    def test_allowed_categories_accepted(self) -> None:
        for cat in ["地点", "组织", "规则", "物品", "技术",
                    "文化", "历史", "自然", "杂项",
                    "place", "organization", "hard_rules", "factions",
                    "social_structure", "力量体系"]:
            saved = project_store.upsert_worldbook(self.db, {
                "project_id": "p1", "title": f"测试-{cat}",
                "category": cat, "content": "x",
            })
            self.assertEqual(saved["category"], cat,
                             f"category {cat!r} should be accepted")

    def test_v1_data_with_banned_category_coerced_on_upgrade(self) -> None:
        """Existing rows with '角色' get UPDATEd to '杂项' on
        ensure_creation_tables, so subsequent reads are clean."""
        db = os.path.join(tempfile.mkdtemp(), "v1wb.db")
        with sqlite3.connect(db) as c:
            c.execute("CREATE TABLE projects (project_id TEXT PRIMARY KEY)")
            # v1 worldbook_entries — no CHECK constraint
            c.execute(
                """CREATE TABLE worldbook_entries (
                       entry_id TEXT PRIMARY KEY,
                       project_id TEXT NOT NULL,
                       title TEXT NOT NULL,
                       category TEXT NOT NULL DEFAULT 'misc',
                       content TEXT NOT NULL DEFAULT '',
                       tags_json TEXT NOT NULL DEFAULT '[]',
                       sort_order INTEGER NOT NULL DEFAULT 0,
                       created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                       updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"""
            )
            c.execute("INSERT INTO projects VALUES ('p1')")
            c.execute(
                "INSERT INTO worldbook_entries (entry_id, project_id, title, category) "
                "VALUES (?, ?, ?, ?)",
                ("wb_bad", "p1", "Alice", "角色"),
            )
            c.commit()

        # Upgrade
        with sqlite3.connect(db) as c:
            ensure_creation_tables(c)
            row = c.execute(
                "SELECT category FROM worldbook_entries WHERE entry_id='wb_bad'"
            ).fetchone()
        self.assertEqual(row[0], "杂项")


if __name__ == "__main__":
    unittest.main()
