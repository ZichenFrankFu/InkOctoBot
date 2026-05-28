"""Tests for ui/backend/app/services/snapshot_reminder.py."""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from storage.project_schema import ensure_creation_tables
from ui.backend.app.services import (
    project_store, snapshot_reminder as reminder, snapshot_store,
)


def _fresh_db_with_char() -> tuple[str, str]:
    db = os.path.join(tempfile.mkdtemp(), "rm.db")
    con = sqlite3.connect(db)
    con.execute("PRAGMA foreign_keys=ON")
    ensure_creation_tables(con)
    con.execute("INSERT INTO projects (project_id, title) VALUES ('p1','t')")
    con.commit()
    con.close()
    char = project_store.upsert_character(
        db, {"project_id": "p1", "name": "李星河"},
    )
    return db, char["id"]


class TestNotStarted(unittest.TestCase):

    def setUp(self) -> None:
        self.db, self.cid = _fresh_db_with_char()
        self.snap = snapshot_store.upsert_snapshot(self.db, {
            "character_id": self.cid, "project_id": "p1",
            "expected_chapter_range_start": 5,
            "expected_chapter_range_end": 10,
            "trigger_description": "重大转折",
        })

    def test_below_lag_no_reminder(self) -> None:
        # 5 + 5 = 10; chapter 10 should NOT trigger yet (>).
        out = reminder.check_overdue_reminders(self.db, "p1", 10)
        self.assertEqual(out, [])

    def test_past_lag_emits_once(self) -> None:
        out = reminder.check_overdue_reminders(self.db, "p1", 11)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["type"], "snapshot_not_started")
        # Second call for same chapter is a no-op.
        out2 = reminder.check_overdue_reminders(self.db, "p1", 11)
        self.assertEqual(out2, [])

    def test_different_chapters_emit_separately(self) -> None:
        reminder.check_overdue_reminders(self.db, "p1", 11)
        out = reminder.check_overdue_reminders(self.db, "p1", 12)
        self.assertEqual(len(out), 1)


class TestTransitionNotCompleted(unittest.TestCase):

    def setUp(self) -> None:
        self.db, self.cid = _fresh_db_with_char()
        self.snap = snapshot_store.upsert_snapshot(self.db, {
            "character_id": self.cid, "project_id": "p1",
            "expected_chapter_range_start": 5,
            "expected_chapter_range_end": 10,
        })
        snapshot_store.bind_chapter(self.db, self.snap["snapshot_id"], 7)

    def test_emits_when_past_expected_end(self) -> None:
        out = reminder.check_overdue_reminders(self.db, "p1", 12)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["type"], "snapshot_transition_not_completed")

    def test_silent_within_range(self) -> None:
        out = reminder.check_overdue_reminders(self.db, "p1", 9)
        self.assertEqual(out, [])

    def test_complete_snapshot_silent(self) -> None:
        snapshot_store.mark_complete(self.db, self.snap["snapshot_id"], 9)
        out = reminder.check_overdue_reminders(self.db, "p1", 30)
        self.assertEqual(out, [])


class TestListAndAcknowledge(unittest.TestCase):

    def setUp(self) -> None:
        self.db, self.cid = _fresh_db_with_char()
        snap = snapshot_store.upsert_snapshot(self.db, {
            "character_id": self.cid, "project_id": "p1",
            "expected_chapter_range_start": 5,
            "expected_chapter_range_end": 10,
        })
        self.sid = snap["snapshot_id"]
        reminder.check_overdue_reminders(self.db, "p1", 11)

    def test_list_outstanding_returns_unacked(self) -> None:
        rows = reminder.list_outstanding_reminders(self.db, "p1")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["snapshot_id"], self.sid)

    def test_acknowledge_marks_done(self) -> None:
        rows = reminder.list_outstanding_reminders(self.db, "p1")
        out = reminder.acknowledge_reminder(
            self.db, rows[0]["reminder_id"], "confirmed",
        )
        self.assertEqual(out["user_acknowledged"], 1)
        self.assertEqual(out["user_action"], "confirmed")
        self.assertEqual(
            reminder.list_outstanding_reminders(self.db, "p1"), [],
        )

    def test_acknowledge_rejects_bad_action(self) -> None:
        rows = reminder.list_outstanding_reminders(self.db, "p1")
        with self.assertRaises(ValueError):
            reminder.acknowledge_reminder(
                self.db, rows[0]["reminder_id"], "bogus",
            )

    def test_acknowledge_returns_none_for_unknown(self) -> None:
        self.assertIsNone(
            reminder.acknowledge_reminder(self.db, "nope", "confirmed"),
        )


if __name__ == "__main__":
    unittest.main()
