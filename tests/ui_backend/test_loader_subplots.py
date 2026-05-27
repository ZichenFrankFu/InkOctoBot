"""Tests for the subplots prompt-context loader (LOADER_SPEC Loader 12)."""
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
from ui.backend.app.services.prompt_context.loaders import subplots as sp_loader


def _fresh_db() -> str:
    db = os.path.join(tempfile.mkdtemp(), "sl.db")
    con = sqlite3.connect(db)
    con.execute("PRAGMA foreign_keys=ON")
    ensure_creation_tables(con)
    con.execute("INSERT INTO projects (project_id, title) VALUES ('p1','t')")
    con.commit()
    con.close()
    return db


def _seed_thread(db: str, *, thread_id: str, name: str, desc: str,
                 status: str = "setup", thread_type: str = "sub",
                 start: int = 1, embedding: list | None = None) -> None:
    with sqlite3.connect(db) as c:
        c.execute(
            "INSERT INTO subplot_threads (thread_id, project_id, name, "
            "description, status, thread_type, start_chapter, "
            "embedding_json, embedding_text_hash) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (thread_id, "p1", name, desc, status, thread_type, start,
             json.dumps(embedding or []),
             sp_loader._hash_text(sp_loader._subplot_embedding_text(
                 {"name": name, "description": desc})) if embedding else ""),
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


class TestSubplotsLoader(unittest.TestCase):

    def setUp(self) -> None:
        self.db = _fresh_db()

    def test_empty_returns_empty(self) -> None:
        with _DBPath(self.db):
            self.assertEqual(sp_loader.load("p1", "X"), "")

    def test_main_lines_always_included(self) -> None:
        _seed_thread(self.db, thread_id="m1", name="主角身世", desc="主线A",
                     thread_type="main", status="building")
        _seed_thread(self.db, thread_id="m2", name="对抗BOSS", desc="主线B",
                     thread_type="main", status="climax")
        with _DBPath(self.db), mock.patch.object(
            sp_loader, "embed_sync", return_value=[]
        ):
            out = sp_loader.load("p1", "X")
        self.assertIn("主线（必须推进或呼应）", out)
        self.assertIn("主角身世", out)
        self.assertIn("对抗BOSS", out)

    def test_subs_ranked_by_cosine(self) -> None:
        _seed_thread(self.db, thread_id="s1", name="支线A", desc="A",
                     embedding=[1.0, 0.0, 0.0])
        _seed_thread(self.db, thread_id="s2", name="支线B", desc="B",
                     embedding=[0.0, 1.0, 0.0])
        _seed_thread(self.db, thread_id="s3", name="支线C", desc="C",
                     embedding=[0.7, 0.7, 0.0])
        with _DBPath(self.db), mock.patch.object(
            sp_loader, "embed_sync", return_value=[[1.0, 0.0, 0.0]]
        ):
            out = sp_loader.load("p1", "X", top_k=3, min_relevance=0.0)
        rec_idx = out.index("相关支线")
        rec = out[rec_idx:]
        self.assertLess(rec.index("支线A"), rec.index("支线C"))
        self.assertLess(rec.index("支线C"), rec.index("支线B"))

    def test_resolved_subs_filtered_out(self) -> None:
        _seed_thread(self.db, thread_id="s_open", name="未结案",
                     desc="X", status="setup", embedding=[1.0, 0.0])
        _seed_thread(self.db, thread_id="s_done", name="已结案",
                     desc="X", status="resolution", embedding=[1.0, 0.0])
        _seed_thread(self.db, thread_id="s_dormant", name="休眠",
                     desc="X", status="dormant", embedding=[1.0, 0.0])
        with _DBPath(self.db), mock.patch.object(
            sp_loader, "embed_sync", return_value=[[1.0, 0.0]]
        ):
            out = sp_loader.load("p1", "X", min_relevance=0.0)
        self.assertIn("未结案", out)
        self.assertNotIn("已结案", out)
        self.assertNotIn("休眠", out)

    def test_exclude_drops_thread(self) -> None:
        _seed_thread(self.db, thread_id="m1", name="主线",
                     desc="", thread_type="main", status="building")
        _seed_thread(self.db, thread_id="s1", name="支线1",
                     desc="x", embedding=[1.0])
        with _DBPath(self.db), mock.patch.object(
            sp_loader, "embed_sync", return_value=[[1.0]]
        ):
            out = sp_loader.load("p1", "X", exclude={"s1"}, min_relevance=0.0)
        self.assertIn("主线", out)
        self.assertNotIn("支线1", out)

    def test_embedding_failure_falls_back_to_newest_first(self) -> None:
        _seed_thread(self.db, thread_id="early", name="老支线",
                     desc="", start=2)
        _seed_thread(self.db, thread_id="recent", name="新支线",
                     desc="", start=20)
        with _DBPath(self.db), mock.patch.object(
            sp_loader, "embed_sync", return_value=[[]]
        ):
            out = sp_loader.load("p1", "X", top_k=2, min_relevance=0.0)
        # Newest (recent) appears before older.
        self.assertLess(out.index("新支线"), out.index("老支线"))

    def test_min_relevance_cuts_off_subs(self) -> None:
        _seed_thread(self.db, thread_id="hi", name="相关高", desc="",
                     embedding=[1.0, 0.0])
        _seed_thread(self.db, thread_id="lo", name="相关低", desc="",
                     embedding=[0.0, 1.0])
        with _DBPath(self.db), mock.patch.object(
            sp_loader, "embed_sync", return_value=[[1.0, 0.0]]
        ):
            out = sp_loader.load("p1", "X", top_k=3, min_relevance=0.9)
        self.assertIn("相关高", out)
        self.assertNotIn("相关低", out)


if __name__ == "__main__":
    unittest.main()
