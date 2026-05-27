"""Tests for reference_injection V1→V2 blob migration + reference_chapters ALTERs."""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from knowledge.reference_db import ReferenceDB
from ui.backend.app.services.prompt_context.loaders.reference import (
    upgrade_reference_injection,
)


class TestUpgrade(unittest.TestCase):

    def test_v2_blob_passes_through_with_defaults(self) -> None:
        v2 = {
            "version": 2,
            "explicit_selections": {"r1": {"characters": True}},
        }
        out = upgrade_reference_injection(v2)
        self.assertEqual(out["version"], 2)
        self.assertEqual(out["explicit_selections"]["r1"]["characters"], True)
        # Defaults filled.
        self.assertIn("auto_top_k", out)
        self.assertIn("min_relevance", out)

    def test_v1_blob_upgraded_with_remap(self) -> None:
        v1 = {
            "selections": {
                "r1": {"characters": True, "settings": True,
                       "plot": False, "rhythm": True},
                "r2": {"characters": False, "settings": False,
                       "plot": True, "rhythm": False},
            },
        }
        out = upgrade_reference_injection(v1)
        self.assertEqual(out["version"], 2)
        self.assertEqual(out["explicit_selections"]["r1"]["characters"], True)
        self.assertEqual(out["explicit_selections"]["r1"]["worldview"], True)
        self.assertEqual(out["explicit_selections"]["r1"]["text_features"], True)
        self.assertEqual(out["explicit_selections"]["r1"]["plot"], False)
        self.assertEqual(out["explicit_selections"]["r1"]["exemplars"], False)

        self.assertEqual(out["explicit_selections"]["r2"]["plot"], True)
        self.assertEqual(out["explicit_selections"]["r2"]["worldview"], False)

    def test_empty_blob_upgrades_to_empty_v2(self) -> None:
        out = upgrade_reference_injection({})
        self.assertEqual(out["version"], 2)
        self.assertEqual(out["explicit_selections"], {})

    def test_idempotent_double_upgrade(self) -> None:
        v1 = {"selections": {"r1": {"settings": True}}}
        first = upgrade_reference_injection(v1)
        second = upgrade_reference_injection(first)
        self.assertEqual(second, first)


class TestReferenceChaptersSchema(unittest.TestCase):

    def test_alter_adds_new_columns(self) -> None:
        db = os.path.join(tempfile.mkdtemp(), "ref.db")
        # Build a v1-style reference_chapters first.
        con = sqlite3.connect(db)
        con.execute(
            "CREATE TABLE reference_chapters ("
            "ref_id TEXT NOT NULL, number INTEGER NOT NULL, "
            "title TEXT, content TEXT, "
            "PRIMARY KEY (ref_id, number))"
        )
        con.execute(
            "INSERT INTO reference_chapters (ref_id, number, title, content) "
            "VALUES ('r1', 1, '第一章', '正文 ABC')"
        )
        con.commit()
        con.close()
        # ReferenceDB.__init__ runs the migration via ensure_reference_tables.
        ReferenceDB(db)
        with sqlite3.connect(db) as c:
            cols = {r[1] for r in c.execute("PRAGMA table_info(reference_chapters)")}
            row = c.execute(
                "SELECT scene_type, embedding_json FROM reference_chapters "
                "WHERE ref_id='r1' AND number=1"
            ).fetchone()
        for col in ("scene_type", "embedding_json", "embedding_text_hash"):
            self.assertIn(col, cols)
        self.assertEqual(row[0], "")
        self.assertEqual(row[1], "[]")


if __name__ == "__main__":
    unittest.main()
