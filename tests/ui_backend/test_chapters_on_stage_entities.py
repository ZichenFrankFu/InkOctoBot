"""Tests for the chapters.on_stage_entities column (LOADER_SPEC Loader 7)."""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from storage.project_schema import ensure_creation_tables
from ui.backend.app.services import project_store
from ui.backend.app.services.prompt_context.chapter_fields import load_chapter_fields


def _fresh_db() -> str:
    db = os.path.join(tempfile.mkdtemp(), "oe.db")
    con = sqlite3.connect(db)
    con.execute("PRAGMA foreign_keys=ON")
    ensure_creation_tables(con)
    con.execute("INSERT INTO projects (project_id, title) VALUES ('p1','t')")
    con.commit()
    con.close()
    return db


def _read_on_stage(db_path: str, chapter_id: str) -> dict:
    with sqlite3.connect(db_path) as c:
        row = c.execute(
            "SELECT on_stage_entities FROM chapters WHERE chapter_id=?",
            (chapter_id,),
        ).fetchone()
    return json.loads(row[0]) if row else {}


class TestSchema(unittest.TestCase):

    def test_alter_is_idempotent(self) -> None:
        db = _fresh_db()
        # ensure_creation_tables already called once via _fresh_db; calling
        # again should be a no-op without raising.
        with sqlite3.connect(db) as c:
            ensure_creation_tables(c)
            cols = {r[1] for r in c.execute("PRAGMA table_info(chapters)")}
        self.assertIn("on_stage_entities", cols)

    def test_backfill_envelope_for_existing_rows(self) -> None:
        """Pre-v3 rows (default '{}') get upgraded to the full envelope
        the next time ensure_creation_tables runs."""
        db = _fresh_db()
        with sqlite3.connect(db) as c:
            c.execute(
                "INSERT INTO chapters (chapter_id, project_id, chapter_num, "
                "volume, characters_json) "
                "VALUES ('ch_legacy','p1',1,1,?)",
                (json.dumps(["李星河", "苏晚"], ensure_ascii=False),),
            )
            c.commit()
            # Row currently has the DEFAULT '{}'. Re-run the migration
            # to trigger the backfill UPDATE.
            ensure_creation_tables(c)
        ose = _read_on_stage(db, "ch_legacy")
        self.assertIn("characters", ose)
        self.assertEqual(ose["characters"], ["李星河", "苏晚"])
        self.assertEqual(ose["locations"], [])


class TestSaveEditorDocBackfill(unittest.TestCase):

    def setUp(self) -> None:
        self.db = _fresh_db()

    def test_derives_from_characters_when_blob_absent(self) -> None:
        project_store.save_editor_doc(self.db, "p1", {
            "volumes": [{"chapters": [{
                "chapter_id": "ch_a", "title": "T",
                "synopsis": "x", "characters": ["张远", "李清漪"],
            }]}],
        })
        ose = _read_on_stage(self.db, "ch_a")
        self.assertEqual(ose["characters"], ["张远", "李清漪"])
        self.assertEqual(ose["locations"], [])
        self.assertEqual(ose["items"], [])
        self.assertEqual(ose["organizations"], [])

    def test_accepts_explicit_full_blob(self) -> None:
        project_store.save_editor_doc(self.db, "p1", {
            "volumes": [{"chapters": [{
                "chapter_id": "ch_b", "title": "T",
                "characters": ["张远"],
                "on_stage_entities": {
                    "characters": ["张远", "李清漪"],
                    "locations": ["幽冥谷"],
                    "items": ["玉佩"],
                    "organizations": ["玄阴宗"],
                },
            }]}],
        })
        ose = _read_on_stage(self.db, "ch_b")
        # Explicit blob wins
        self.assertEqual(set(ose["characters"]), {"张远", "李清漪"})
        self.assertEqual(ose["locations"], ["幽冥谷"])
        self.assertEqual(ose["items"], ["玉佩"])
        self.assertEqual(ose["organizations"], ["玄阴宗"])


class TestChapterFieldsLoader(unittest.TestCase):

    def setUp(self) -> None:
        self.db = _fresh_db()
        project_store.save_editor_doc(self.db, "p1", {
            "volumes": [{"chapters": [{
                "id": "ch_x", "chapter_id": "ch_x", "synopsis": "本章",
                "characters": ["张远"],
                "on_stage_entities": {
                    "characters": ["张远", "李清漪"],
                    "locations": ["幽冥谷"],
                    "items": [],
                    "organizations": [],
                },
            }]}],
        })
        self._patcher = mock.patch(
            "ui.backend.app.services.project_paths.get_db_path",
            return_value=self.db,
        )
        self._patcher.start()

    def tearDown(self) -> None:
        self._patcher.stop()

    def test_exposes_on_stage_entities(self) -> None:
        fields = load_chapter_fields("p1", "ch_x")
        ose = fields["on_stage_entities"]
        self.assertEqual(set(ose["characters"]), {"张远", "李清漪"})
        self.assertEqual(ose["locations"], ["幽冥谷"])

    def test_missing_chapter_returns_envelope_defaults(self) -> None:
        fields = load_chapter_fields("p1", "ch_missing")
        ose = fields["on_stage_entities"]
        self.assertEqual(ose["characters"], [])
        self.assertEqual(ose["locations"], [])


if __name__ == "__main__":
    unittest.main()
