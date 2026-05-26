"""
Tests for the editor doc + generic blob adapters on project_store.

Editor doc behaviour under test:
  - PUT writes BOTH project_blobs(scope='editor') AND chapter rows
    in chapters table (denormalized)
  - chapter rows track removals (vanished chapters get DELETEd)
  - GET reads back from project_blobs
"""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from storage.project_schema import ensure_creation_tables
from ui.backend.app.services import project_store


def _fresh_db() -> str:
    db = os.path.join(tempfile.mkdtemp(), "editor_store.db")
    con = sqlite3.connect(db)
    con.execute("PRAGMA foreign_keys=ON")
    ensure_creation_tables(con)
    con.execute("INSERT INTO projects (project_id, title) VALUES ('p1', 't')")
    con.commit()
    con.close()
    return db


class TestEditorDoc(unittest.TestCase):

    def setUp(self) -> None:
        self.db = _fresh_db()

    def _doc(self) -> dict:
        return {
            "volumes": [{
                "id": "v1", "title": "第一卷", "order": 0,
                "chapters": [
                    {"id": "c1", "title": "第一章", "order": 0,
                     "outline": "大纲一",
                     "content": "第一章正文，共 100 字。" * 4,
                     "synopsis": "梗概一",
                     "time": "春", "location": "村口",
                     "characters": ["李星河", "苏晚"],
                     "pov_character": "李星河"},
                    {"id": "c2", "title": "第二章", "order": 1,
                     "outline": "大纲二",
                     "content": "正文二"},
                ],
            }],
        }

    def test_put_get_round_trip(self) -> None:
        res = project_store.save_editor_doc(self.db, "p1", self._doc())
        self.assertIn("saved_at", res)

        loaded = project_store.get_editor_doc(self.db, "p1")
        self.assertEqual(len(loaded["volumes"]), 1)
        chapters = loaded["volumes"][0]["chapters"]
        self.assertEqual([c["title"] for c in chapters], ["第一章", "第二章"])
        self.assertEqual(chapters[0]["content"][:5], "第一章正文")

    def test_chapters_table_mirrored(self) -> None:
        project_store.save_editor_doc(self.db, "p1", self._doc())
        with sqlite3.connect(self.db) as c:
            c.row_factory = sqlite3.Row
            rows = c.execute(
                "SELECT chapter_num, title, outline, final_text, "
                "synopsis, time_label, location, pov_character "
                "FROM chapters WHERE project_id='p1' ORDER BY chapter_num"
            ).fetchall()
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["title"], "第一章")
        self.assertEqual(rows[0]["outline"], "大纲一")
        self.assertEqual(rows[0]["synopsis"], "梗概一")
        self.assertEqual(rows[0]["time_label"], "春")
        self.assertEqual(rows[0]["pov_character"], "李星河")
        self.assertTrue(rows[0]["final_text"].startswith("第一章正文"))

    def test_chapter_removal_propagates(self) -> None:
        project_store.save_editor_doc(self.db, "p1", self._doc())
        # Drop ch2
        doc = self._doc()
        doc["volumes"][0]["chapters"] = doc["volumes"][0]["chapters"][:1]
        project_store.save_editor_doc(self.db, "p1", doc)
        with sqlite3.connect(self.db) as c:
            n = c.execute(
                "SELECT COUNT(*) FROM chapters WHERE project_id='p1'"
            ).fetchone()[0]
        self.assertEqual(n, 1)

    def test_empty_doc_returns_default(self) -> None:
        loaded = project_store.get_editor_doc(self.db, "unknown_proj")
        self.assertEqual(loaded, {"volumes": []})


class TestBlob(unittest.TestCase):

    def setUp(self) -> None:
        self.db = _fresh_db()

    def test_get_default(self) -> None:
        self.assertEqual(
            project_store.get_blob(self.db, "p1", "calibration",
                                   default={"x": 1}),
            {"x": 1},
        )

    def test_save_and_get(self) -> None:
        project_store.save_blob(self.db, "p1", "calibration",
                                {"tone": 60, "saved_at": 123})
        data = project_store.get_blob(self.db, "p1", "calibration")
        self.assertEqual(data["tone"], 60)

    def test_overwrite(self) -> None:
        project_store.save_blob(self.db, "p1", "calibration", {"v": 1})
        project_store.save_blob(self.db, "p1", "calibration", {"v": 2})
        self.assertEqual(
            project_store.get_blob(self.db, "p1", "calibration")["v"], 2,
        )

    def test_delete(self) -> None:
        project_store.save_blob(self.db, "p1", "scope1", {"x": 1})
        project_store.delete_blob(self.db, "p1", "scope1")
        self.assertEqual(
            project_store.get_blob(self.db, "p1", "scope1"), {},
        )

    def test_different_scopes_isolated(self) -> None:
        project_store.save_blob(self.db, "p1", "calibration", {"v": "A"})
        project_store.save_blob(self.db, "p1", "reference_injection", {"v": "B"})
        self.assertEqual(
            project_store.get_blob(self.db, "p1", "calibration")["v"], "A",
        )
        self.assertEqual(
            project_store.get_blob(self.db, "p1", "reference_injection")["v"], "B",
        )


if __name__ == "__main__":
    unittest.main()
