"""
Tests for the creation/memory database schema.
"""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import uuid
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from storage.project_schema import ensure_creation_tables, CREATION_DDL


class TestCreationSchema(unittest.TestCase):

    def setUp(self):
        self.db_path = os.path.join(tempfile.mkdtemp(), "test.db")
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        ensure_creation_tables(self.conn)

    def tearDown(self):
        self.conn.close()

    def test_all_tables_created(self):
        tables = [r[0] for r in self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()]
        expected = [
            "projects", "chapters", "text_versions", "chapter_summaries",
            "episodic_events", "information_events",
            "user_style_preferences", "constraint_rules",
        ]
        for t in expected:
            self.assertIn(t, tables, f"Missing table: {t}")
        # v2 redesign: permanent_facts was removed (replaced by
        # truth_current_state). See docs/SCHEMA_REDESIGN.md.
        self.assertNotIn("permanent_facts", tables)
        # episodic_events lost two columns in v2.
        ep_cols = {r[1] for r in self.conn.execute("PRAGMA table_info(episodic_events)")}
        self.assertNotIn("foreshadow_status", ep_cols)
        self.assertNotIn("foreshadow_target_chapter", ep_cols)

    def test_project_crud(self):
        pid = f"proj_{uuid.uuid4().hex[:8]}"
        self.conn.execute(
            "INSERT INTO projects (project_id, title, genre, logline) VALUES (?, ?, ?, ?)",
            (pid, "测试小说", "仙侠", "一个修仙少年的故事"),
        )
        self.conn.commit()
        row = self.conn.execute("SELECT * FROM projects WHERE project_id=?", (pid,)).fetchone()
        self.assertEqual(row["title"], "测试小说")
        self.assertEqual(row["status"], "planning")

    def test_chapter_crud(self):
        pid = "proj_test"
        self.conn.execute("INSERT INTO projects (project_id, title) VALUES (?, ?)", (pid, "T"))
        self.conn.execute(
            "INSERT INTO chapters (chapter_id, project_id, chapter_num, title) VALUES (?, ?, ?, ?)",
            ("ch1", pid, 1, "第一章"),
        )
        self.conn.commit()
        row = self.conn.execute("SELECT * FROM chapters WHERE chapter_id='ch1'").fetchone()
        self.assertEqual(row["chapter_num"], 1)
        self.assertEqual(row["status"], "draft")

    def test_chapter_unique_constraint(self):
        pid = "proj_unique"
        self.conn.execute("INSERT INTO projects (project_id, title) VALUES (?, ?)", (pid, "T"))
        self.conn.execute(
            "INSERT INTO chapters (chapter_id, project_id, chapter_num) VALUES (?, ?, ?)",
            ("ch_a", pid, 1),
        )
        self.conn.commit()
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                "INSERT INTO chapters (chapter_id, project_id, chapter_num) VALUES (?, ?, ?)",
                ("ch_b", pid, 1),
            )

    def test_foreign_key_cascade(self):
        pid = "proj_cascade"
        self.conn.execute("INSERT INTO projects (project_id, title) VALUES (?, ?)", (pid, "T"))
        self.conn.execute(
            "INSERT INTO chapters (chapter_id, project_id, chapter_num) VALUES (?, ?, ?)",
            ("ch_del", pid, 1),
        )
        self.conn.commit()
        self.conn.execute("DELETE FROM projects WHERE project_id=?", (pid,))
        self.conn.commit()
        row = self.conn.execute("SELECT * FROM chapters WHERE chapter_id='ch_del'").fetchone()
        self.assertIsNone(row)

    def test_episodic_events(self):
        # v2 schema: foreshadow_status / foreshadow_target_chapter columns
        # were dropped (data moved to pending_hooks). 'foreshadowing' is
        # no longer a valid event_type — write a 'plot' row instead.
        pid = "proj_ep"
        self.conn.execute("INSERT INTO projects (project_id, title) VALUES (?, ?)", (pid, "T"))
        self.conn.execute(
            """INSERT INTO episodic_events (event_id, project_id, chapter_num, event_type, description,
               importance) VALUES (?, ?, ?, ?, ?, ?)""",
            ("ev1", pid, 1, "plot", "重要剧情节点", 4),
        )
        self.conn.commit()
        row = self.conn.execute("SELECT * FROM episodic_events WHERE event_id='ev1'").fetchone()
        self.assertEqual(row["event_type"], "plot")
        self.assertEqual(row["importance"], 4)

    def test_constraint_rules(self):
        pid = "proj_con"
        self.conn.execute("INSERT INTO projects (project_id, title) VALUES (?, ?)", (pid, "T"))
        self.conn.execute(
            """INSERT INTO constraint_rules (rule_id, project_id, rule_type, priority, description)
               VALUES (?, ?, ?, ?, ?)""",
            ("r1", pid, "world_rule", 1, "没有枪械"),
        )
        self.conn.commit()
        row = self.conn.execute("SELECT * FROM constraint_rules WHERE rule_id='r1'").fetchone()
        self.assertEqual(row["rule_type"], "world_rule")
        self.assertEqual(row["is_active"], 1)

    def test_idempotent_creation(self):
        # Running ensure_creation_tables twice should not error
        ensure_creation_tables(self.conn)
        ensure_creation_tables(self.conn)


class TestSecurityModules(unittest.TestCase):

    def test_data_isolation(self):
        from security.test_mode_isolation import DataIsolation
        tmpdir = tempfile.mkdtemp()
        iso = DataIsolation(tmpdir)
        proj_dir = iso.create_project_dir("test_proj")
        self.assertTrue(proj_dir.exists())
        self.assertTrue((proj_dir / "chapters").exists())
        self.assertTrue((proj_dir / "characters").exists())
        self.assertTrue(iso.project_exists("test_proj"))
        self.assertIn("test_proj", iso.list_projects())

        size = iso.get_project_size("test_proj")
        self.assertTrue(size["exists"])

        iso.cleanup_project("test_proj", keep_exports=False)
        self.assertFalse(iso.project_exists("test_proj"))


if __name__ == "__main__":
    unittest.main()
