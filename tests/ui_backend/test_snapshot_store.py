"""Tests for ui/backend/app/services/snapshot_store.py."""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from storage.project_schema import ensure_creation_tables
from ui.backend.app.services import project_store, snapshot_store


def _fresh_db_with_char() -> tuple[str, str]:
    db = os.path.join(tempfile.mkdtemp(), "snap.db")
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


class TestCRUD(unittest.TestCase):

    def test_upsert_appends_when_order_omitted(self) -> None:
        db, cid = _fresh_db_with_char()
        s1 = snapshot_store.upsert_snapshot(db, {
            "character_id": cid, "project_id": "p1",
            "trigger_description": "首次转变",
        })
        s2 = snapshot_store.upsert_snapshot(db, {
            "character_id": cid, "project_id": "p1",
            "trigger_description": "二次转变",
        })
        self.assertEqual(s1["snapshot_order"], 1)
        self.assertEqual(s2["snapshot_order"], 2)

    def test_upsert_round_trips_all_fields(self) -> None:
        db, cid = _fresh_db_with_char()
        s = snapshot_store.upsert_snapshot(db, {
            "character_id": cid, "project_id": "p1",
            "expected_chapter_range_start": 5,
            "expected_chapter_range_end": 8,
            "trigger_description": "得知身世",
            "personality_override": "阴沉",
            "speech_style_override": "寡言",
            "alias": "陈墨",
            "layer_b_overrides": {"loss_aversion": 3.0},
            "relations_overrides": {
                "李清漪": {"label": "信任降低", "sentiment_score": 60},
            },
            "other_changes": "身世背景：神秘组织遗孤",
            "bound_chapters": [6, 7, 8],
            "transition_complete_chapter": 8,
            "bound_by": "user",
        })
        got = snapshot_store.get_snapshot(db, s["snapshot_id"])
        self.assertEqual(got["alias"], "陈墨")
        self.assertEqual(got["layer_b_overrides"]["loss_aversion"], 3.0)
        self.assertEqual(got["relations_overrides"]["李清漪"]["label"], "信任降低")
        self.assertEqual(got["bound_chapters"], [6, 7, 8])
        self.assertEqual(got["transition_complete_chapter"], 8)

    def test_update_via_snapshot_id_preserves_order(self) -> None:
        db, cid = _fresh_db_with_char()
        s = snapshot_store.upsert_snapshot(db, {
            "character_id": cid, "project_id": "p1",
        })
        updated = snapshot_store.upsert_snapshot(db, {
            "snapshot_id": s["snapshot_id"],
            "character_id": cid,
            "personality_override": "改了",
        })
        self.assertEqual(updated["snapshot_order"], 1)
        self.assertEqual(updated["personality_override"], "改了")

    def test_unique_order_per_character_enforced(self) -> None:
        db, cid = _fresh_db_with_char()
        snapshot_store.upsert_snapshot(db, {
            "character_id": cid, "project_id": "p1", "snapshot_order": 1,
        })
        with self.assertRaises(ValueError):
            snapshot_store.upsert_snapshot(db, {
                "character_id": cid, "project_id": "p1", "snapshot_order": 1,
            })

    def test_delete_removes_row(self) -> None:
        db, cid = _fresh_db_with_char()
        s = snapshot_store.upsert_snapshot(db, {
            "character_id": cid, "project_id": "p1",
        })
        snapshot_store.delete_snapshot(db, s["snapshot_id"])
        self.assertIsNone(snapshot_store.get_snapshot(db, s["snapshot_id"]))

    def test_cascade_delete_when_character_removed(self) -> None:
        db, cid = _fresh_db_with_char()
        snapshot_store.upsert_snapshot(db, {
            "character_id": cid, "project_id": "p1",
        })
        project_store.delete_character(db, cid)
        self.assertEqual(snapshot_store.list_snapshots(db, cid), [])


class TestBindAndComplete(unittest.TestCase):

    def setUp(self) -> None:
        self.db, self.cid = _fresh_db_with_char()
        self.snap = snapshot_store.upsert_snapshot(self.db, {
            "character_id": self.cid, "project_id": "p1",
        })
        self.sid = self.snap["snapshot_id"]

    def test_bind_chapter_dedupes_and_sorts(self) -> None:
        snapshot_store.bind_chapter(self.db, self.sid, 7)
        snapshot_store.bind_chapter(self.db, self.sid, 3)
        snapshot_store.bind_chapter(self.db, self.sid, 7)  # dup
        out = snapshot_store.get_snapshot(self.db, self.sid)
        self.assertEqual(out["bound_chapters"], [3, 7])

    def test_unbind_chapter(self) -> None:
        snapshot_store.bind_chapter(self.db, self.sid, 5)
        snapshot_store.bind_chapter(self.db, self.sid, 6)
        snapshot_store.unbind_chapter(self.db, self.sid, 5)
        out = snapshot_store.get_snapshot(self.db, self.sid)
        self.assertEqual(out["bound_chapters"], [6])

    def test_mark_complete_auto_binds_chapter(self) -> None:
        out = snapshot_store.mark_complete(self.db, self.sid, 12)
        self.assertEqual(out["transition_complete_chapter"], 12)
        self.assertIn(12, out["bound_chapters"])

    def test_unmark_complete(self) -> None:
        snapshot_store.mark_complete(self.db, self.sid, 12)
        snapshot_store.unmark_complete(self.db, self.sid)
        out = snapshot_store.get_snapshot(self.db, self.sid)
        self.assertIsNone(out["transition_complete_chapter"])

    def test_auto_bind_preserves_user_bound_by(self) -> None:
        # First bind by user
        snapshot_store.bind_chapter(self.db, self.sid, 5, source="user")
        # Auto-source bind shouldn't downgrade bound_by
        snapshot_store.bind_chapter(self.db, self.sid, 7, source="auto")
        out = snapshot_store.get_snapshot(self.db, self.sid)
        self.assertEqual(out["bound_by"], "user")
        self.assertEqual(out["bound_chapters"], [5, 7])


class TestExtrasMigration(unittest.TestCase):

    def test_migrates_dynamic_snapshots_from_extras(self) -> None:
        db = os.path.join(tempfile.mkdtemp(), "mig.db")
        con = sqlite3.connect(db)
        con.execute("PRAGMA foreign_keys=ON")
        ensure_creation_tables(con)
        con.execute("INSERT INTO projects (project_id, title) VALUES ('p1','t')")
        con.commit()
        con.close()
        # Build a character with the legacy dynamic_snapshots payload
        # written into extras directly (bypassing the new strip path).
        cid = project_store.upsert_character(db, {
            "project_id": "p1", "name": "Legacy",
        })["id"]
        with sqlite3.connect(db) as c:
            c.execute(
                "UPDATE characters SET extra_json = ? WHERE character_id = ?",
                (json.dumps({
                    "dynamic_snapshots": [
                        {
                            "chapter": "第3章",
                            "personality": "改了",
                            "layer_b": {"loss_aversion": 3.0},
                            "hidden_identities": [
                                {"name": "陈墨", "revealed_to": ["李清漪"]},
                            ],
                        },
                    ],
                }), cid),
            )
            c.commit()
        n = snapshot_store.migrate_dynamic_snapshots_extras(db)
        self.assertEqual(n, 1)
        snaps = snapshot_store.list_snapshots(db, cid)
        self.assertEqual(len(snaps), 1)
        self.assertEqual(snaps[0]["personality_override"], "改了")
        self.assertEqual(snaps[0]["expected_chapter_range_start"], 3)
        self.assertEqual(snaps[0]["bound_chapters"], [3])
        hidden = snaps[0]["relations_overrides"].get("__hidden_identities__")
        self.assertEqual(hidden[0]["name"], "陈墨")
        # Re-running is idempotent.
        n2 = snapshot_store.migrate_dynamic_snapshots_extras(db)
        self.assertEqual(n2, 0)


if __name__ == "__main__":
    unittest.main()
