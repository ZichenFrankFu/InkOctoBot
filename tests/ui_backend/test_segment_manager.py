"""Unit tests for ui/backend/app/services/segment_manager.py."""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from storage.project_schema import ensure_creation_tables
from ui.backend.app.services import segment_manager


def _fresh_db_with_chapter() -> tuple[str, str]:
    db = os.path.join(tempfile.mkdtemp(), "sm.db")
    con = sqlite3.connect(db)
    con.execute("PRAGMA foreign_keys=ON")
    ensure_creation_tables(con)
    con.execute("INSERT INTO projects (project_id, title) VALUES ('p1','t')")
    con.execute(
        "INSERT INTO chapters (chapter_id, project_id, chapter_num, volume) "
        "VALUES ('ch1','p1',1,1)"
    )
    con.commit()
    con.close()
    return db, "ch1"


class TestSegmentLifecycle(unittest.TestCase):

    def setUp(self) -> None:
        self.db, self.cid = _fresh_db_with_chapter()

    def test_empty_chapter_returns_empty_list(self) -> None:
        self.assertEqual(segment_manager.get_segments(self.db, self.cid), [])

    def test_replace_segments_round_trip(self) -> None:
        out = segment_manager.replace_segments(self.db, self.cid, "p1", [
            {"content": "P1.", "source": "user_written"},
            {"content": "P2.", "source": "ai_generated"},
        ])
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0]["source"], "user_written")
        self.assertEqual(out[0]["sequence_order"], 0)
        self.assertEqual(out[1]["sequence_order"], 1)

    def test_invalid_source_rejected(self) -> None:
        with self.assertRaises(ValueError):
            segment_manager.replace_segments(self.db, self.cid, "p1", [
                {"content": "x", "source": "unknown_source"},
            ])

    def test_update_chapter_content_creates_segments(self) -> None:
        out = segment_manager.update_chapter_content(
            self.db, self.cid, "p1", "第一段。\n\n第二段。",
        )
        self.assertEqual(len(out), 2)
        # Brand-new content → user_written (no prior AI segments to inherit from)
        for s in out:
            self.assertEqual(s["source"], "user_written")

    def test_diff_preserves_unchanged_segment_source(self) -> None:
        segment_manager.replace_segments(self.db, self.cid, "p1", [
            {"content": "AI 段落 A。", "source": "ai_generated", "generation_id": "g1"},
            {"content": "AI 段落 B。", "source": "ai_generated", "generation_id": "g1"},
        ])
        # User saves the chapter unchanged
        out = segment_manager.update_chapter_content(
            self.db, self.cid, "p1", "AI 段落 A。\n\nAI 段落 B。",
        )
        self.assertEqual(out[0]["source"], "ai_generated")
        self.assertEqual(out[1]["source"], "ai_generated")
        self.assertEqual(out[0]["generation_id"], "g1")

    def test_diff_promotes_ai_to_ai_user_edited_on_edit(self) -> None:
        segment_manager.replace_segments(self.db, self.cid, "p1", [
            {"content": "AI 段落，原本是这样写的。", "source": "ai_generated",
             "generation_id": "g1"},
        ])
        out = segment_manager.update_chapter_content(
            self.db, self.cid, "p1", "AI 段落，原本是这样写的，user 改了几个字。",
        )
        self.assertEqual(out[0]["source"], "ai_user_edited")
        # original_ai_content should be preserved so future regenerations can compare
        self.assertEqual(out[0]["original_ai_content"], "AI 段落，原本是这样写的。")

    def test_diff_marks_brand_new_paragraph_as_user_written(self) -> None:
        segment_manager.replace_segments(self.db, self.cid, "p1", [
            {"content": "AI 段落 A。", "source": "ai_generated"},
        ])
        out = segment_manager.update_chapter_content(
            self.db, self.cid, "p1", "AI 段落 A。\n\n用户手写的新段落，跟前段无关。",
        )
        sources = [s["source"] for s in out]
        self.assertIn("user_written", sources)

    def test_record_ai_generation_appends(self) -> None:
        segment_manager.replace_segments(self.db, self.cid, "p1", [
            {"content": "用户开头。", "source": "user_written"},
        ])
        out = segment_manager.record_ai_generation(
            self.db, self.cid, "p1",
            ["AI 段 1.", "AI 段 2."], generation_id="g42",
        )
        self.assertEqual(len(out), 3)
        self.assertEqual(out[1]["source"], "ai_generated")
        self.assertEqual(out[1]["generation_id"], "g42")

    def test_cascade_delete_on_chapter_delete(self) -> None:
        segment_manager.replace_segments(self.db, self.cid, "p1", [
            {"content": "x", "source": "user_written"},
        ])
        with sqlite3.connect(self.db) as c:
            c.execute("PRAGMA foreign_keys = ON")
            c.execute("DELETE FROM chapters WHERE chapter_id=?", (self.cid,))
            c.commit()
        self.assertEqual(segment_manager.get_segments(self.db, self.cid), [])


if __name__ == "__main__":
    unittest.main()
