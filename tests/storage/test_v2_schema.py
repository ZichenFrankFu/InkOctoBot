"""
Tests for the v2 project schema additions:
  - new canonical tables (characters, worldbook_entries, project_memories,
    storyline_nodes/edges, writing_knowledge, chat_messages, project_blobs)
  - chapters table extended columns (synopsis/time_label/location/...)
  - ALTER-on-upgrade path from v1 -> v2
  - FK cascade + UNIQUE constraints

See docs/SCHEMA_REDESIGN.md for the design.
"""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from storage.project_schema import (
    CREATION_DDL,
    _CHAPTERS_V2_COLUMNS,
    _ensure_chapter_v2_columns,
    ensure_creation_tables,
)


def _conn(path: str) -> sqlite3.Connection:
    c = sqlite3.connect(path)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    return c


class TestV2Tables(unittest.TestCase):

    def setUp(self) -> None:
        self.db_path = os.path.join(tempfile.mkdtemp(), "v2.db")
        self.conn = _conn(self.db_path)
        ensure_creation_tables(self.conn)
        self.conn.execute(
            "INSERT INTO projects (project_id, title) VALUES (?, ?)",
            ("p1", "test"),
        )
        self.conn.commit()

    def tearDown(self) -> None:
        self.conn.close()

    def _tables(self) -> set[str]:
        return {r[0] for r in self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}

    def test_all_v2_tables_present(self) -> None:
        tables = self._tables()
        for t in [
            "characters", "worldbook_entries", "project_memories",
            "storyline_nodes", "storyline_edges",
            "writing_knowledge", "chat_messages", "project_blobs",
        ]:
            self.assertIn(t, tables, f"v2 table missing: {t}")

    def test_chapters_has_v2_columns(self) -> None:
        cols = {row["name"] for row in self.conn.execute(
            "PRAGMA table_info(chapters)"
        )}
        for col, _ in _CHAPTERS_V2_COLUMNS:
            self.assertIn(col, cols, f"chapters missing v2 column: {col}")

    def test_characters_unique_name_per_project(self) -> None:
        self.conn.execute(
            """INSERT INTO characters (character_id, project_id, name)
               VALUES (?, ?, ?)""",
            ("c1", "p1", "主角A"),
        )
        self.conn.commit()
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                """INSERT INTO characters (character_id, project_id, name)
                   VALUES (?, ?, ?)""",
                ("c2", "p1", "主角A"),
            )

    def test_characters_same_name_different_projects_ok(self) -> None:
        self.conn.execute(
            "INSERT INTO projects (project_id, title) VALUES (?, ?)",
            ("p2", "other"),
        )
        self.conn.execute(
            "INSERT INTO characters (character_id, project_id, name) VALUES (?, ?, ?)",
            ("c1", "p1", "主角A"),
        )
        self.conn.execute(
            "INSERT INTO characters (character_id, project_id, name) VALUES (?, ?, ?)",
            ("c2", "p2", "主角A"),
        )
        self.conn.commit()
        rows = self.conn.execute(
            "SELECT COUNT(*) FROM characters WHERE name=?", ("主角A",)
        ).fetchone()
        self.assertEqual(rows[0], 2)

    def test_worldbook_crud_and_cascade(self) -> None:
        self.conn.execute(
            "INSERT INTO worldbook_entries (entry_id, project_id, title, category, content) VALUES (?, ?, ?, ?, ?)",
            ("wb1", "p1", "星门", "hard_rules", "远古传送装置"),
        )
        self.conn.commit()
        row = self.conn.execute(
            "SELECT * FROM worldbook_entries WHERE entry_id='wb1'"
        ).fetchone()
        self.assertEqual(row["category"], "hard_rules")

        # Cascade delete
        self.conn.execute("DELETE FROM projects WHERE project_id='p1'")
        self.conn.commit()
        self.assertIsNone(self.conn.execute(
            "SELECT 1 FROM worldbook_entries WHERE entry_id='wb1'"
        ).fetchone())

    def test_project_blobs_unique_project_scope(self) -> None:
        self.conn.execute(
            "INSERT INTO project_blobs (blob_id, project_id, scope, data_json) VALUES (?, ?, ?, ?)",
            ("p1__editor", "p1", "editor", "{}"),
        )
        self.conn.commit()
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                "INSERT INTO project_blobs (blob_id, project_id, scope, data_json) VALUES (?, ?, ?, ?)",
                ("other_id", "p1", "editor", "{}"),
            )

    def test_storyline_edges_cascade_with_nodes(self) -> None:
        self.conn.execute(
            "INSERT INTO storyline_nodes (node_id, project_id) VALUES ('n1', 'p1')"
        )
        self.conn.execute(
            "INSERT INTO storyline_nodes (node_id, project_id) VALUES ('n2', 'p1')"
        )
        self.conn.execute(
            "INSERT INTO storyline_edges (edge_id, project_id, from_node_id, to_node_id) VALUES ('e1', 'p1', 'n1', 'n2')"
        )
        self.conn.commit()
        self.conn.execute("DELETE FROM storyline_nodes WHERE node_id='n1'")
        self.conn.commit()
        self.assertIsNone(self.conn.execute(
            "SELECT 1 FROM storyline_edges WHERE edge_id='e1'"
        ).fetchone())

    def test_chat_messages_role_check(self) -> None:
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                "INSERT INTO chat_messages (message_id, project_id, role, content) VALUES (?, ?, ?, ?)",
                ("m1", "p1", "bogus_role", ""),
            )

    def test_writing_knowledge_no_project_id(self) -> None:
        # Cross-project: no FK
        self.conn.execute(
            "INSERT INTO writing_knowledge (knowledge_id, domain, title) VALUES (?, ?, ?)",
            ("wk1", "pacing", "节奏控制"),
        )
        self.conn.commit()
        row = self.conn.execute(
            "SELECT * FROM writing_knowledge WHERE knowledge_id='wk1'"
        ).fetchone()
        self.assertEqual(row["domain"], "pacing")

    def test_idempotent_ensure(self) -> None:
        ensure_creation_tables(self.conn)
        ensure_creation_tables(self.conn)


class TestV1ToV2Upgrade(unittest.TestCase):
    """A v1 chapters table (without v2 columns) is upgraded in place."""

    def test_chapters_alter_adds_v2_columns(self) -> None:
        db_path = os.path.join(tempfile.mkdtemp(), "v1.db")
        with _conn(db_path) as c:
            # Bootstrap a deliberately minimal "v1" chapters table.
            c.execute(
                """CREATE TABLE projects (project_id TEXT PRIMARY KEY,
                                          title TEXT NOT NULL DEFAULT '')"""
            )
            c.execute(
                """CREATE TABLE chapters (
                       chapter_id TEXT PRIMARY KEY,
                       project_id TEXT NOT NULL,
                       chapter_num INTEGER NOT NULL,
                       title TEXT NOT NULL DEFAULT '',
                       outline TEXT NOT NULL DEFAULT '',
                       final_text TEXT NOT NULL DEFAULT '',
                       word_count INTEGER NOT NULL DEFAULT 0
                   )"""
            )
            c.commit()
            _ensure_chapter_v2_columns(c)
            c.commit()
            cols = {row[1] for row in c.execute("PRAGMA table_info(chapters)")}
        for col, _ in _CHAPTERS_V2_COLUMNS:
            self.assertIn(col, cols)


if __name__ == "__main__":
    unittest.main()
