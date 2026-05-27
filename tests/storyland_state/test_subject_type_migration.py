"""Tests for the truth_current_state.subject_type column (LOADER_SPEC Loader 10)."""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from storage.project_schema import ensure_creation_tables
from knowledge.storyland_state.schemas import StatePatch, StorylandStateDeltas
from knowledge.storyland_state.store import StorylandStateStore


def _fresh_db() -> str:
    db = os.path.join(tempfile.mkdtemp(), "st.db")
    con = sqlite3.connect(db)
    con.execute("PRAGMA foreign_keys=ON")
    ensure_creation_tables(con)
    con.execute("INSERT INTO projects (project_id, title) VALUES ('p1','t')")
    con.commit()
    con.close()
    return db


class TestSchema(unittest.TestCase):

    def test_column_present_on_fresh_db(self) -> None:
        db = _fresh_db()
        with sqlite3.connect(db) as c:
            cols = {r[1] for r in c.execute("PRAGMA table_info(truth_current_state)")}
        self.assertIn("subject_type", cols)

    def test_index_present(self) -> None:
        db = _fresh_db()
        with sqlite3.connect(db) as c:
            idxs = {r[1] for r in c.execute(
                "SELECT type, name FROM sqlite_master WHERE type='index' "
                "AND tbl_name='truth_current_state'"
            )}
        self.assertIn("idx_state_subject_type", idxs)

    def test_migration_idempotent_on_repeat_ensure(self) -> None:
        db = _fresh_db()
        with sqlite3.connect(db) as c:
            ensure_creation_tables(c)
            cols = {r[1] for r in c.execute("PRAGMA table_info(truth_current_state)")}
        self.assertIn("subject_type", cols)

    def test_heuristic_backfill_classifies_location_from_worldbook(self) -> None:
        """Pre-v3 row whose subject is a registered location → 'location'."""
        # Hand-build a v2-style DB (no subject_type column) then upgrade.
        db = os.path.join(tempfile.mkdtemp(), "v2.db")
        con = sqlite3.connect(db)
        con.execute("PRAGMA foreign_keys=ON")
        # Bootstrap projects + worldbook minimally (so the heuristic can join).
        con.execute("""CREATE TABLE projects (
            project_id TEXT PRIMARY KEY, title TEXT
        )""")
        con.execute("INSERT INTO projects VALUES ('p1','t')")
        con.execute("""CREATE TABLE worldbook_entries (
            entry_id TEXT PRIMARY KEY, project_id TEXT, title TEXT,
            category TEXT, content TEXT
        )""")
        con.execute(
            "INSERT INTO worldbook_entries VALUES "
            "('w1','p1','幽冥谷','地点','禁地')"
        )
        # Create v2-style truth_current_state (no subject_type column).
        con.execute("""CREATE TABLE truth_current_state (
            triple_id TEXT PRIMARY KEY,
            project_id TEXT,
            subject TEXT, predicate TEXT, object TEXT,
            valid_from_chapter INTEGER, valid_to_chapter INTEGER,
            superseded_by TEXT, confidence REAL DEFAULT 1.0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""")
        con.execute(
            "INSERT INTO truth_current_state(triple_id, project_id, subject, "
            "predicate, object, valid_from_chapter) VALUES "
            "('t1','p1','幽冥谷','物理状态','正常',5)"
        )
        con.execute(
            "INSERT INTO truth_current_state(triple_id, project_id, subject, "
            "predicate, object, valid_from_chapter) VALUES "
            "('t2','p1','张远','在','青云山',1)"
        )
        con.commit()
        # Run the v3 migration
        ensure_creation_tables(con)
        rows = {r[0]: r[1] for r in con.execute(
            "SELECT triple_id, subject_type FROM truth_current_state"
        )}
        con.close()
        self.assertEqual(rows["t1"], "location")     # heuristic hit
        self.assertEqual(rows["t2"], "character")    # default


class TestStatePatchSubjectType(unittest.TestCase):

    def setUp(self) -> None:
        self.db = _fresh_db()
        self.store = StorylandStateStore("p1", db_path=self.db)

    def test_default_is_character(self) -> None:
        p = StatePatch(subject="张远", predicate="在", object="青云山",
                       valid_from_chapter=5)
        self.assertEqual(p.subject_type, "character")

    def test_explicit_location_persists(self) -> None:
        self.store.apply_deltas(StorylandStateDeltas(
            chapter_num=5,
            current_state_patches=[
                StatePatch(subject="幽冥谷", predicate="物理状态",
                           object="山顶被打平", valid_from_chapter=5,
                           subject_type="location"),
            ],
        ))
        with sqlite3.connect(self.db) as c:
            row = c.execute(
                "SELECT subject_type FROM truth_current_state "
                "WHERE subject=?", ("幽冥谷",),
            ).fetchone()
        self.assertEqual(row[0], "location")

    def test_default_character_persists_when_unset(self) -> None:
        self.store.apply_deltas(StorylandStateDeltas(
            chapter_num=5,
            current_state_patches=[
                StatePatch(subject="张远", predicate="情绪",
                           object="平静", valid_from_chapter=5),
            ],
        ))
        with sqlite3.connect(self.db) as c:
            row = c.execute(
                "SELECT subject_type FROM truth_current_state "
                "WHERE subject=?", ("张远",),
            ).fetchone()
        self.assertEqual(row[0], "character")


if __name__ == "__main__":
    unittest.main()
