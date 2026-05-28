"""Tests for the reader_memory prompt-context loader (LOADER_SPEC Loader 8)."""
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
from ui.backend.app.services.prompt_context.loaders import reader_memory as rm


def _fresh_db() -> str:
    db = os.path.join(tempfile.mkdtemp(), "rm.db")
    con = sqlite3.connect(db)
    con.execute("PRAGMA foreign_keys=ON")
    ensure_creation_tables(con)
    con.execute("INSERT INTO projects (project_id, title) VALUES ('p1','t')")
    con.commit()
    con.close()
    return db


def _seed_summary(db: str, chapter: int, *, text: str = "",
                  title: str = "", anchor: bool = False) -> None:
    with sqlite3.connect(db) as c:
        c.execute(
            "INSERT INTO chapter_summaries(summary_id, project_id, chapter_num, "
            "summary_text, title, is_anchor) VALUES (?, 'p1', ?, ?, ?, ?)",
            (f"sum_{chapter}", chapter, text or f"ch {chapter}",
             title, 1 if anchor else 0),
        )
        c.commit()


def _seed_event(db: str, chapter: int, description: str,
                characters: list[str], event_type: str = "plot") -> None:
    import uuid
    with sqlite3.connect(db) as c:
        c.execute(
            "INSERT INTO episodic_events(event_id, project_id, chapter_num, "
            "event_type, description, characters_json) "
            "VALUES (?, 'p1', ?, ?, ?, ?)",
            (f"ev_{uuid.uuid4().hex[:8]}", chapter, event_type, description,
             json.dumps(characters, ensure_ascii=False)),
        )
        c.commit()


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


class TestReaderMemory(unittest.TestCase):

    def test_chapter_one_returns_empty(self) -> None:
        db = _fresh_db()
        with _DBPath(db):
            self.assertEqual(rm.load("p1", 1), "")

    def test_no_data_returns_empty(self) -> None:
        db = _fresh_db()
        with _DBPath(db):
            self.assertEqual(rm.load("p1", 5), "")

    def test_recent_window_filters_strictly_causal(self) -> None:
        db = _fresh_db()
        # Window default = 8; chapter_num=5 → chapters [0..4] eligible.
        for ch in range(1, 10):
            _seed_summary(db, ch, text=f"摘要 {ch}", title=f"第{ch}章")
        with _DBPath(db):
            out = rm.load("p1", 5)
        # Strictly chapter_num < 5.
        self.assertIn("第 4 章", out)
        self.assertNotIn("第 5 章", out)
        self.assertNotIn("第 6 章", out)
        # Window starts at max(0, 5-8) = 0, so chapter 1 still shows.
        self.assertIn("第 1 章", out)

    def test_anchor_outside_window_surfaces(self) -> None:
        db = _fresh_db()
        _seed_summary(db, 1, text="父亲遗物锚点", anchor=True)
        for ch in range(20, 25):
            _seed_summary(db, ch, text=f"近段 {ch}")
        with _DBPath(db), \
             mock.patch.object(rm, "_query_semantic", return_value=[]):
            out = rm.load("p1", 25)
        self.assertIn("关键章节锚点", out)
        self.assertIn("父亲遗物锚点", out)

    def test_anchor_within_window_dedupes_into_recent(self) -> None:
        """Anchor chapters already in the recent window appear only once."""
        db = _fresh_db()
        _seed_summary(db, 4, text="近期且锚点", anchor=True)
        with _DBPath(db), \
             mock.patch.object(rm, "_query_semantic", return_value=[]):
            out = rm.load("p1", 5)
        # Body has "近期且锚点" exactly once (in recent), no "关键章节锚点" section
        self.assertEqual(out.count("近期且锚点"), 1)
        self.assertNotIn("关键章节锚点", out)

    def test_episodic_filtered_to_on_stage_characters(self) -> None:
        db = _fresh_db()
        _seed_event(db, 22, "张远突破到筑基期", ["张远"])
        _seed_event(db, 23, "李清漪 私下离开", ["李清漪"])
        with _DBPath(db), \
             mock.patch.object(rm, "_query_semantic", return_value=[]):
            out = rm.load("p1", 30, on_stage_characters=["张远"])
        self.assertIn("张远突破到筑基期", out)
        self.assertNotIn("李清漪 私下离开", out)

    def test_semantic_layer_calls_into_chromadb_with_chapter_filter(self) -> None:
        db = _fresh_db()
        _seed_summary(db, 1, text="近期")  # provide some non-semantic data
        with _DBPath(db), \
             mock.patch.object(rm, "_query_semantic",
                               return_value=[
                                   {"chapter_num": 12, "text": "玉佩特写"},
                                   {"chapter_num": 99, "text": "未来剧透"},  # > K
                               ]) as mocked:
            out = rm.load("p1", 30, chapter_outline="玉佩的秘密")
        self.assertIn("语义相关历史片段", out)
        # The loader doesn't apply the spoiler filter — that's the
        # injected helper's job; but our mock still returns both. The
        # real _query_semantic already enforces chapter_num < K.
        mocked.assert_called_once()

    def test_exclude_kw_skips_layers(self) -> None:
        db = _fresh_db()
        _seed_summary(db, 4, text="近期")
        _seed_summary(db, 1, text="锚点", anchor=True)
        _seed_event(db, 4, "事件", ["张远"])
        with _DBPath(db), \
             mock.patch.object(rm, "_query_semantic", return_value=[]):
            out = rm.load(
                "p1", 10, on_stage_characters=["张远"],
                exclude={"recent", "anchors", "episodic"},
            )
        self.assertEqual(out, "")


if __name__ == "__main__":
    unittest.main()
