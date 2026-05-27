"""Tests for the current_chapter_draft loader (LOADER_SPEC Loader 9)."""
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
from ui.backend.app.services import segment_manager, failure_analyzer
from ui.backend.app.services.prompt_context.loaders import (
    current_chapter_draft as ccd,
)


def _fresh_db_with_chapter() -> tuple[str, str]:
    db = os.path.join(tempfile.mkdtemp(), "ccd.db")
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


class _DBPath:
    def __init__(self, db: str) -> None:
        self._patcher = mock.patch(
            "ui.backend.app.services.project_paths.get_db_path",
            return_value=db,
        )

    def __enter__(self):
        self._patcher.start()
        return self

    def __exit__(self, *exc):
        self._patcher.stop()


class TestEmpty(unittest.TestCase):

    def test_empty_chapter_id_returns_empty(self) -> None:
        db, _ = _fresh_db_with_chapter()
        with _DBPath(db):
            self.assertEqual(ccd.load("p1", ""), "")

    def test_no_segments_continue_returns_empty(self) -> None:
        db, cid = _fresh_db_with_chapter()
        with _DBPath(db):
            self.assertEqual(
                ccd.load("p1", cid, generation_mode="continue"), "",
            )


class TestFreshMode(unittest.TestCase):

    def setUp(self) -> None:
        self.db, self.cid = _fresh_db_with_chapter()

    def test_keeps_user_and_ai_user_edited_drops_ai_only(self) -> None:
        segment_manager.replace_segments(self.db, self.cid, "p1", [
            {"content": "用户开头。", "source": "user_written"},
            {"content": "AI 中段未修改。", "source": "ai_generated"},
            {"content": "AI 末段，用户改过几个字。", "source": "ai_user_edited"},
        ])
        with _DBPath(self.db):
            out = ccd.load("p1", self.cid, generation_mode="fresh")
        self.assertIn("用户开头", out)
        self.assertIn("AI 末段", out)
        self.assertNotIn("AI 中段未修改", out)

    def test_gap_summary_reports_missing_indices(self) -> None:
        segment_manager.replace_segments(self.db, self.cid, "p1", [
            {"content": "P0.", "source": "user_written"},
            {"content": "P1.", "source": "ai_generated"},
            {"content": "P2.", "source": "ai_generated"},
            {"content": "P3.", "source": "user_written"},
        ])
        with _DBPath(self.db):
            out = ccd.load("p1", self.cid, generation_mode="fresh")
        # Kept indices: 0 and 3 → gap is 1-2
        self.assertIn("段落 1-2", out)

    def test_anti_hints_from_recent_failures_injected(self) -> None:
        fid = failure_analyzer.archive_failed_generation(
            self.db, self.cid, "p1", "失败正文",
        )
        with sqlite3.connect(self.db) as c:
            c.execute(
                "UPDATE chapter_failed_generations SET issues_detected=? "
                "WHERE failed_gen_id=?",
                (json.dumps(["开场内心独白过长", "对话太露骨"]), fid),
            )
            c.commit()
        with _DBPath(self.db):
            out = ccd.load("p1", self.cid, generation_mode="fresh")
        self.assertIn("避免：开场内心独白过长", out)
        self.assertIn("避免：对话太露骨", out)

    def test_skipping_failed_drafts_disables_anti_hints(self) -> None:
        fid = failure_analyzer.archive_failed_generation(
            self.db, self.cid, "p1", "X",
        )
        with sqlite3.connect(self.db) as c:
            c.execute(
                "UPDATE chapter_failed_generations SET issues_detected=? "
                "WHERE failed_gen_id=?",
                (json.dumps(["X 问题"]), fid),
            )
            c.commit()
        with _DBPath(self.db):
            out = ccd.load(
                "p1", self.cid, generation_mode="fresh",
                include_failed_drafts_as_hint=False,
            )
        self.assertNotIn("X 问题", out)


class TestContinueMode(unittest.TestCase):

    def test_returns_tail_with_total_chars_label(self) -> None:
        db, cid = _fresh_db_with_chapter()
        segment_manager.replace_segments(db, cid, "p1", [
            {"content": "前段。", "source": "user_written"},
            {"content": "末段。", "source": "ai_generated"},
        ])
        with _DBPath(db):
            out = ccd.load("p1", cid, generation_mode="continue")
        self.assertIn("续写", out)
        self.assertIn("已写", out)
        self.assertIn("末段", out)

    def test_long_chapter_drops_head_with_omission_note(self) -> None:
        # 5 paragraphs of 1500 chars each → 7500 total; tail = 3500
        db, cid = _fresh_db_with_chapter()
        segment_manager.replace_segments(db, cid, "p1", [
            {"content": "A" * 1500, "source": "user_written"},
            {"content": "B" * 1500, "source": "user_written"},
            {"content": "C" * 1500, "source": "ai_generated"},
            {"content": "D" * 1500, "source": "ai_generated"},
            {"content": "E" * 1500, "source": "ai_generated"},
        ])
        with _DBPath(db):
            out = ccd.load("p1", cid, generation_mode="continue")
        self.assertIn("...（前", out)
        self.assertIn("段省略）", out)
        self.assertIn("E" * 10, out)


class TestRewriteFromMode(unittest.TestCase):

    def setUp(self) -> None:
        self.db, self.cid = _fresh_db_with_chapter()
        segment_manager.replace_segments(self.db, self.cid, "p1", [
            {"content": "段0。", "source": "user_written"},
            {"content": "段1。", "source": "user_written"},
            {"content": "段2。", "source": "ai_generated"},
            {"content": "段3。", "source": "ai_generated"},
        ])

    def test_by_paragraph_index(self) -> None:
        with _DBPath(self.db):
            out = ccd.load(
                "p1", self.cid, generation_mode="rewrite_from",
                revision_anchor={"paragraph_index": 2},
            )
        self.assertIn("从第 3 段开始重写", out)
        self.assertIn("段0", out)
        self.assertIn("段1", out)
        # Discarded text shouldn't appear as content; mentioned only in anti-hint.
        self.assertIn("避免重复方向", out)
        self.assertIn("段2", out)  # anti-hint quote

    def test_by_anchor_text(self) -> None:
        with _DBPath(self.db):
            out = ccd.load(
                "p1", self.cid, generation_mode="rewrite_from",
                revision_anchor={"anchor_text": "段2"},
            )
        self.assertIn("从第 3 段开始重写", out)

    def test_anchor_not_found_degrades_to_continue_from_end(self) -> None:
        with _DBPath(self.db):
            out = ccd.load(
                "p1", self.cid, generation_mode="rewrite_from",
                revision_anchor={"anchor_text": "完全不存在"},
            )
        self.assertIn("从第 5 段开始重写", out)


class TestModifySectionMode(unittest.TestCase):

    def setUp(self) -> None:
        self.db, self.cid = _fresh_db_with_chapter()
        segment_manager.replace_segments(self.db, self.cid, "p1", [
            {"content": "段0。", "source": "user_written"},
            {"content": "段1（要改）。", "source": "ai_generated"},
            {"content": "段2（要改）。", "source": "ai_generated"},
            {"content": "段3。", "source": "user_written"},
        ])

    def test_renders_before_target_after_with_instruction(self) -> None:
        with _DBPath(self.db):
            out = ccd.load(
                "p1", self.cid, generation_mode="modify_section",
                revision_anchor={
                    "start_paragraph": 1,
                    "end_paragraph": 2,
                    "modification_instruction": "把节奏加快、把情绪做出来",
                },
            )
        self.assertIn("[前文 - 保留]", out)
        self.assertIn("段0", out)
        self.assertIn("待修改段落（第 2-3 段", out)
        self.assertIn("段1（要改）", out)
        self.assertIn("把节奏加快", out)
        self.assertIn("[后文 - 保留", out)
        self.assertIn("段3", out)


if __name__ == "__main__":
    unittest.main()
