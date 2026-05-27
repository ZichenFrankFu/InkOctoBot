"""Tests for ui/backend/app/services/skill_index.py."""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from storage.project_schema import ensure_creation_tables
from ui.backend.app.services import skill_index


def _fresh_db() -> str:
    db = os.path.join(tempfile.mkdtemp(), "si.db")
    con = sqlite3.connect(db)
    con.execute("PRAGMA foreign_keys=ON")
    ensure_creation_tables(con)
    con.execute("INSERT INTO projects (project_id, title) VALUES ('p1','t')")
    con.commit()
    con.close()
    skill_index.reset_sync_cache()
    return db


class TestCRUD(unittest.TestCase):

    def setUp(self) -> None:
        self.db = _fresh_db()

    def test_upsert_get_list_delete(self) -> None:
        out = skill_index.upsert_skill(self.db, {
            "skill_id": "钩子设计",
            "display_name": "钩子设计",
            "description": "如何在章末抛钩",
            "kind": "builtin",
            "body_snippet": "钩子分三类...",
        })
        self.assertEqual(out["display_name"], "钩子设计")

        got = skill_index.get_skill(self.db, "钩子设计")
        self.assertEqual(got["description"], "如何在章末抛钩")

        rows = skill_index.list_skills(self.db)
        self.assertEqual(len(rows), 1)

        skill_index.delete_skill(self.db, "钩子设计")
        self.assertIsNone(skill_index.get_skill(self.db, "钩子设计"))

    def test_canonical_text_change_clears_embedding(self) -> None:
        skill_index.upsert_skill(self.db, {
            "skill_id": "k", "display_name": "K",
            "description": "原描述", "kind": "builtin",
            "body_snippet": "body",
        })
        skill_index.set_embedding(self.db, "k", [0.1, 0.2], "hash1")
        # Re-upsert with new description → canonical text changes → embedding cleared.
        skill_index.upsert_skill(self.db, {
            "skill_id": "k", "display_name": "K",
            "description": "新描述", "kind": "builtin",
            "body_snippet": "body",
        })
        got = skill_index.get_skill(self.db, "k", include_embedding=True)
        self.assertEqual(got["embedding"], [])
        self.assertEqual(got["embedding_text_hash"], "")

    def test_canonical_text_unchanged_preserves_embedding(self) -> None:
        skill_index.upsert_skill(self.db, {
            "skill_id": "k", "display_name": "K",
            "description": "d", "kind": "builtin", "body_snippet": "b",
        })
        text = skill_index.skill_embedding_text({
            "display_name": "K", "description": "d", "body_snippet": "b",
        })
        skill_index.set_embedding(
            self.db, "k", [0.5, 0.6],
            skill_index.hash_embedding_text(text),
        )
        # Re-upsert with same canonical → embedding survives.
        skill_index.upsert_skill(self.db, {
            "skill_id": "k", "display_name": "K",
            "description": "d", "kind": "builtin", "body_snippet": "b",
        })
        got = skill_index.get_skill(self.db, "k", include_embedding=True)
        self.assertEqual(got["embedding"], [0.5, 0.6])


class TestPins(unittest.TestCase):

    def setUp(self) -> None:
        self.db = _fresh_db()
        skill_index.upsert_skill(self.db, {
            "skill_id": "k", "display_name": "K",
            "description": "d", "kind": "builtin",
        })

    def test_pin_unpin(self) -> None:
        skill_index.pin_skill(self.db, "p1", "k")
        self.assertEqual(
            skill_index.list_pinned_skill_ids(self.db, "p1"),
            ["k"],
        )
        skill_index.pin_skill(self.db, "p1", "k")  # idempotent
        self.assertEqual(
            len(skill_index.list_pinned_skill_ids(self.db, "p1")), 1,
        )
        skill_index.unpin_skill(self.db, "p1", "k")
        self.assertEqual(skill_index.list_pinned_skill_ids(self.db, "p1"), [])

    def test_pins_scoped_per_project(self) -> None:
        with sqlite3.connect(self.db) as c:
            c.execute("INSERT INTO projects(project_id,title) VALUES ('p2','t')")
            c.commit()
        skill_index.pin_skill(self.db, "p1", "k")
        self.assertEqual(skill_index.list_pinned_skill_ids(self.db, "p1"), ["k"])
        self.assertEqual(skill_index.list_pinned_skill_ids(self.db, "p2"), [])

    def test_skill_delete_cascades_pin(self) -> None:
        skill_index.pin_skill(self.db, "p1", "k")
        skill_index.delete_skill(self.db, "k")
        self.assertEqual(skill_index.list_pinned_skill_ids(self.db, "p1"), [])


class TestSyncFromRegistry(unittest.TestCase):

    def setUp(self) -> None:
        self.db = _fresh_db()

    def test_sync_inserts_and_removes(self) -> None:
        # First snapshot: 2 skills.
        with mock.patch.object(skill_index, "_registry_snapshot",
                               return_value=[
                                   {"skill_id": "a", "display_name": "A",
                                    "description": "x", "kind": "builtin",
                                    "body_snippet": "ax"},
                                   {"skill_id": "b", "display_name": "B",
                                    "description": "y", "kind": "learned",
                                    "body_snippet": "by"},
                               ]):
            n = skill_index.sync_from_registry(self.db)
        self.assertEqual(n, 2)
        self.assertEqual(
            sorted(s["skill_id"] for s in skill_index.list_skills(self.db)),
            ["a", "b"],
        )

        # Second snapshot drops 'a' → its mirror row goes away.
        skill_index.reset_sync_cache()
        with mock.patch.object(skill_index, "_registry_snapshot",
                               return_value=[
                                   {"skill_id": "b", "display_name": "B",
                                    "description": "y", "kind": "learned",
                                    "body_snippet": "by"},
                               ]):
            skill_index.sync_from_registry(self.db)
        self.assertEqual(
            [s["skill_id"] for s in skill_index.list_skills(self.db)],
            ["b"],
        )

    def test_sync_is_cached_within_process(self) -> None:
        with mock.patch.object(skill_index, "_registry_snapshot",
                               return_value=[]) as mocked:
            skill_index.sync_from_registry(self.db)
            skill_index.sync_from_registry(self.db)  # cached
            self.assertEqual(mocked.call_count, 1)


if __name__ == "__main__":
    unittest.main()
