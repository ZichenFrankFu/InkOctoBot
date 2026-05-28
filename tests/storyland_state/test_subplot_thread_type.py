"""Tests for subplot_threads.thread_type + embedding columns (LOADER_SPEC Loader 12)."""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from storage.project_schema import ensure_creation_tables
from storage.truth_schema import ensure_truth_tables


def _fresh_db() -> str:
    db = os.path.join(tempfile.mkdtemp(), "sp.db")
    con = sqlite3.connect(db)
    con.execute("PRAGMA foreign_keys=ON")
    ensure_creation_tables(con)
    con.execute("INSERT INTO projects (project_id, title) VALUES ('p1','t')")
    con.commit()
    con.close()
    return db


class TestSchema(unittest.TestCase):

    def test_columns_present_on_fresh_db(self) -> None:
        db = _fresh_db()
        with sqlite3.connect(db) as c:
            cols = {r[1] for r in c.execute("PRAGMA table_info(subplot_threads)")}
        for col in ("thread_type", "embedding_json", "embedding_text_hash"):
            self.assertIn(col, cols)

    def test_index_on_proj_type_present(self) -> None:
        db = _fresh_db()
        with sqlite3.connect(db) as c:
            idxs = {
                r[1] for r in c.execute(
                    "SELECT type, name FROM sqlite_master WHERE type='index' "
                    "AND tbl_name='subplot_threads'"
                )
            }
        self.assertIn("idx_subplots_proj_type", idxs)

    def test_default_thread_type_is_sub(self) -> None:
        db = _fresh_db()
        with sqlite3.connect(db) as c:
            c.execute(
                "INSERT INTO subplot_threads (thread_id, project_id, name, "
                "start_chapter) VALUES ('t1','p1','支线1',5)"
            )
            c.commit()
            row = c.execute(
                "SELECT thread_type FROM subplot_threads WHERE thread_id='t1'"
            ).fetchone()
        self.assertEqual(row[0], "sub")

    def test_check_constraint_rejects_unknown_type(self) -> None:
        db = _fresh_db()
        with sqlite3.connect(db) as c:
            with self.assertRaises(sqlite3.IntegrityError):
                c.execute(
                    "INSERT INTO subplot_threads (thread_id, project_id, name, "
                    "start_chapter, thread_type) VALUES "
                    "('t2','p1','x',5,'bogus')"
                )

    def test_v2_db_upgrades_via_alter(self) -> None:
        """Pre-v3 subplot_threads (no thread_type column) gets upgraded."""
        db = os.path.join(tempfile.mkdtemp(), "v2.db")
        con = sqlite3.connect(db)
        con.execute("""CREATE TABLE projects (
            project_id TEXT PRIMARY KEY, title TEXT
        )""")
        con.execute("INSERT INTO projects VALUES ('p1','t')")
        con.execute("""CREATE TABLE subplot_threads (
            thread_id TEXT PRIMARY KEY,
            project_id TEXT,
            name TEXT NOT NULL,
            description TEXT,
            status TEXT,
            start_chapter INTEGER NOT NULL
        )""")
        con.execute(
            "INSERT INTO subplot_threads (thread_id, project_id, name, "
            "status, start_chapter) VALUES ('legacy','p1','旧支线','setup',3)"
        )
        con.commit()
        ensure_truth_tables(con)
        cols = {r[1] for r in con.execute("PRAGMA table_info(subplot_threads)")}
        for col in ("thread_type", "embedding_json", "embedding_text_hash"):
            self.assertIn(col, cols)
        row = con.execute(
            "SELECT thread_type FROM subplot_threads WHERE thread_id='legacy'"
        ).fetchone()
        # ALTER default is 'sub'.
        self.assertEqual(row[0], "sub")
        con.close()


if __name__ == "__main__":
    unittest.main()
