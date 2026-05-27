"""Tests for the inspiration prompt-context loader (LOADER_SPEC Loader 4)."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from knowledge.idea_db import (
    IdeaDB, inspiration_embedding_text, hash_embedding_text,
)
from ui.backend.app.services.prompt_context.loaders import inspiration as ins_loader


def _seed_db_with_path() -> tuple[IdeaDB, str]:
    path = os.path.join(tempfile.mkdtemp(), "idea.db")
    return IdeaDB(path), path


class _MockPath:
    """Context manager that points the loader at a temp idea.db."""

    def __init__(self, path: str) -> None:
        self.path = path
        self._patcher = mock.patch.object(
            ins_loader, "_idea_db_path", return_value=path,
        )

    def __enter__(self):
        self._patcher.start()
        return self

    def __exit__(self, *exc):
        self._patcher.stop()


class TestEmptyAndExclude(unittest.TestCase):

    def test_no_rows_returns_empty(self) -> None:
        _, path = _seed_db_with_path()
        with _MockPath(path):
            self.assertEqual(ins_loader.load("p1", "大纲"), "")

    def test_excluded_ids_dropped(self) -> None:
        db, path = _seed_db_with_path()
        r = db.create_inspiration("场景", "T", "幽冥谷", project_id="p1")
        # Pre-set embedding so we don't hit the network.
        text = inspiration_embedding_text(r)
        db.set_embedding(r["id"], [1.0, 0.0, 0.0], hash_embedding_text(text))
        with _MockPath(path), mock.patch.object(
            ins_loader, "embed_sync", return_value=[[1.0, 0.0, 0.0]],
        ):
            out = ins_loader.load(
                "p1", "幽冥谷相关", exclude={r["id"]},
            )
        self.assertEqual(out, "")


class TestPinnedAndScope(unittest.TestCase):

    def test_user_pinned_force_included(self) -> None:
        db, path = _seed_db_with_path()
        a = db.create_inspiration("场景", "T", "无关内容", project_id="p1")
        with _MockPath(path), mock.patch.object(
            ins_loader, "embed_sync", return_value=[[1.0]],
        ):
            out = ins_loader.load(
                "p1", "完全无关的大纲",
                user_pinned_ids=[a["id"]],
                min_relevance=0.99,
            )
        self.assertIn("用户显式关联", out)
        self.assertIn("无关内容", out)

    def test_global_visible_to_project(self) -> None:
        db, path = _seed_db_with_path()
        g = db.create_inspiration("场景", "G", "全局灵感正文")
        text = inspiration_embedding_text(g)
        db.set_embedding(g["id"], [1.0, 0.0], hash_embedding_text(text))
        with _MockPath(path), mock.patch.object(
            ins_loader, "embed_sync", return_value=[[1.0, 0.0]],
        ):
            out = ins_loader.load("any_project", "查询")
        self.assertIn("全局灵感正文", out)


class TestRanking(unittest.TestCase):

    def setUp(self) -> None:
        self.db, self.path = _seed_db_with_path()
        # Three rows with hand-picked vectors so the ordering is
        # deterministic against [1, 0, 0].
        self.r1 = self.db.create_inspiration(
            "场景", "T1", "和主题最贴", project_id="p1",
        )
        self.r2 = self.db.create_inspiration(
            "对话", "T2", "中等相关", project_id="p1",
        )
        self.r3 = self.db.create_inspiration(
            "设定", "T3", "完全无关", project_id="p1",
        )
        for row, vec in (
            (self.r1, [1.0, 0.0, 0.0]),
            (self.r2, [0.7, 0.7, 0.0]),
            (self.r3, [0.0, 0.0, 1.0]),
        ):
            text = inspiration_embedding_text(row)
            self.db.set_embedding(row["id"], vec, hash_embedding_text(text))

    def test_top_k_ordered_by_similarity(self) -> None:
        with _MockPath(self.path), mock.patch.object(
            ins_loader, "embed_sync", return_value=[[1.0, 0.0, 0.0]],
        ):
            out = ins_loader.load("p1", "X", top_k=2, min_relevance=0.0)
        # Most-similar first within the recommended section
        rec_start = out.index("系统推荐")
        rec_body = out[rec_start:]
        self.assertLess(rec_body.index("和主题最贴"), rec_body.index("中等相关"))
        self.assertNotIn("完全无关", rec_body)

    def test_min_relevance_cuts_off(self) -> None:
        with _MockPath(self.path), mock.patch.object(
            ins_loader, "embed_sync", return_value=[[1.0, 0.0, 0.0]],
        ):
            out = ins_loader.load("p1", "X", top_k=3, min_relevance=0.95)
        # Only r1 clears 0.95 → r2 (~0.7) and r3 (0) drop.
        self.assertIn("和主题最贴", out)
        self.assertNotIn("中等相关", out)
        self.assertNotIn("完全无关", out)

    def test_embedding_failure_falls_back_to_newest_first(self) -> None:
        with _MockPath(self.path), mock.patch.object(
            ins_loader, "embed_sync", return_value=[[]],
        ):
            out = ins_loader.load("p1", "X", top_k=3, min_relevance=0.0)
        # We still get *some* recommended block, in newest-first order.
        self.assertIn("系统推荐", out)
        self.assertIn("完全无关", out)


class TestUsedTooOften(unittest.TestCase):

    def test_over_mined_dropped_from_recommendations(self) -> None:
        db, path = _seed_db_with_path()
        r = db.create_inspiration("场景", "T", "被反复使用", project_id="p1")
        text = inspiration_embedding_text(r)
        db.set_embedding(r["id"], [1.0, 0.0], hash_embedding_text(text))
        db.mark_used_in_chapter(r["id"], 7)
        db.mark_used_in_chapter(r["id"], 7)
        with _MockPath(path), mock.patch.object(
            ins_loader, "embed_sync", return_value=[[1.0, 0.0]],
        ):
            out = ins_loader.load("p1", "X", chapter_num=7, min_relevance=0.0)
        self.assertEqual(out, "")

    def test_used_elsewhere_still_recommended(self) -> None:
        db, path = _seed_db_with_path()
        r = db.create_inspiration("场景", "T", "X 章用过两次", project_id="p1")
        text = inspiration_embedding_text(r)
        db.set_embedding(r["id"], [1.0, 0.0], hash_embedding_text(text))
        db.mark_used_in_chapter(r["id"], 3)
        db.mark_used_in_chapter(r["id"], 3)
        with _MockPath(path), mock.patch.object(
            ins_loader, "embed_sync", return_value=[[1.0, 0.0]],
        ):
            out = ins_loader.load("p1", "X", chapter_num=7, min_relevance=0.0)
        self.assertIn("X 章用过两次", out)


class TestTagsRendering(unittest.TestCase):

    def test_tags_listed_in_bracket_prefix(self) -> None:
        db, path = _seed_db_with_path()
        r = db.create_inspiration(
            "场景", "T", "内容ABC", project_id="p1",
            tags=["场景", "氛围"],
        )
        text = inspiration_embedding_text(r)
        db.set_embedding(r["id"], [1.0, 0.0], hash_embedding_text(text))
        with _MockPath(path), mock.patch.object(
            ins_loader, "embed_sync", return_value=[[1.0, 0.0]],
        ):
            out = ins_loader.load("p1", "X", min_relevance=0.0)
        self.assertIn("[场景 / 氛围]", out)


if __name__ == "__main__":
    unittest.main()
