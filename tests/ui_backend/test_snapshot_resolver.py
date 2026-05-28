"""Tests for ui/backend/app/services/character_snapshot_resolver.py."""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from storage.project_schema import ensure_creation_tables
from ui.backend.app.services import (
    character_snapshot_resolver as resolver,
    project_store,
    snapshot_store,
)


def _fresh_db_with_char() -> tuple[str, str]:
    db = os.path.join(tempfile.mkdtemp(), "rs.db")
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


class TestResolver(unittest.TestCase):

    def setUp(self) -> None:
        self.db, self.cid = _fresh_db_with_char()

    def _add_snap(self, **kwargs):
        return snapshot_store.upsert_snapshot(self.db, {
            "character_id": self.cid, "project_id": "p1",
            **kwargs,
        })

    def test_no_snapshots_is_stable_with_no_baseline(self) -> None:
        out = resolver.resolve(self.db, self.cid, 5)
        self.assertEqual(out["transition_status"], "stable")
        self.assertIsNone(out["baseline_snapshot"])
        self.assertIsNone(out["in_transition"])

    def test_event_when_chapter_is_a_bound_beat(self) -> None:
        self._add_snap(
            bound_chapters=[28, 30, 32],
            personality_override="阴沉",
        )
        out = resolver.resolve(self.db, self.cid, 30)
        self.assertEqual(out["transition_status"], "transition_event")
        self.assertEqual(out["in_transition"]["personality_override"], "阴沉")

    def test_gap_when_chapter_falls_between_bound_beats(self) -> None:
        self._add_snap(bound_chapters=[28, 30, 32])
        out = resolver.resolve(self.db, self.cid, 29)
        self.assertEqual(out["transition_status"], "transition_gap")

    def test_complete_when_chapter_is_complete_chapter(self) -> None:
        self._add_snap(
            bound_chapters=[28, 30, 32],
            transition_complete_chapter=32,
            personality_override="新状态",
        )
        out = resolver.resolve(self.db, self.cid, 32)
        self.assertEqual(out["transition_status"], "transition_complete")
        self.assertIsNotNone(out["in_transition"])

    def test_stable_after_complete_chapter(self) -> None:
        self._add_snap(
            bound_chapters=[28, 30, 32],
            transition_complete_chapter=32,
            personality_override="新基线",
        )
        out = resolver.resolve(self.db, self.cid, 40)
        self.assertEqual(out["transition_status"], "stable")
        self.assertIsNotNone(out["baseline_snapshot"])
        self.assertEqual(out["baseline_snapshot"]["personality_override"], "新基线")

    def test_baseline_picks_most_recent_completed(self) -> None:
        self._add_snap(
            snapshot_order=1, bound_chapters=[5, 7],
            transition_complete_chapter=7, personality_override="阶段一",
        )
        self._add_snap(
            snapshot_order=2, bound_chapters=[12, 14],
            transition_complete_chapter=14, personality_override="阶段二",
        )
        out = resolver.resolve(self.db, self.cid, 20)
        self.assertEqual(out["baseline_snapshot"]["personality_override"], "阶段二")
        self.assertEqual(out["transition_status"], "stable")

    def test_previous_snapshot_resolution(self) -> None:
        self._add_snap(
            snapshot_order=1, bound_chapters=[5, 6],
            transition_complete_chapter=6, personality_override="阶段一",
        )
        self._add_snap(
            snapshot_order=2, bound_chapters=[12, 14],
            personality_override="阶段二（转中）",
        )
        out = resolver.resolve(self.db, self.cid, 12)
        self.assertEqual(out["transition_status"], "transition_event")
        self.assertIsNotNone(out["previous_snapshot"])
        self.assertEqual(
            out["previous_snapshot"]["personality_override"], "阶段一",
        )

    def test_chapter_after_complete_belongs_to_next_baseline(self) -> None:
        """Snapshot 1 completes at ch 7; snapshot 2 starts beats at 12.
        chapter 9 should be stable with snapshot 1 as baseline."""
        self._add_snap(
            snapshot_order=1, bound_chapters=[5, 7],
            transition_complete_chapter=7, personality_override="阶段一",
        )
        self._add_snap(
            snapshot_order=2, bound_chapters=[12, 14],
            personality_override="阶段二",
        )
        out = resolver.resolve(self.db, self.cid, 9)
        self.assertEqual(out["transition_status"], "stable")
        self.assertEqual(out["baseline_snapshot"]["personality_override"], "阶段一")

    def test_single_bound_chapter_event_not_gap(self) -> None:
        """A snapshot with only one bound chapter triggers event on that
        chapter — never gap (gap needs ≥ 2 bound chapters)."""
        self._add_snap(bound_chapters=[10], personality_override="一次性")
        out_on = resolver.resolve(self.db, self.cid, 10)
        out_off = resolver.resolve(self.db, self.cid, 11)
        self.assertEqual(out_on["transition_status"], "transition_event")
        self.assertEqual(out_off["transition_status"], "stable")


if __name__ == "__main__":
    unittest.main()
