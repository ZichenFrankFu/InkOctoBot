"""Unit tests for the prompt_context loaders that changed:

- platform_market: placeholder (always returns "")
- character_cards: Layer B + snapshot-driven hidden_identities + chapter-aware snapshot pick
- worldbook: outline-driven similarity injection, empty synopsis short-circuit
"""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from storage.project_schema import ensure_creation_tables
from ui.backend.app.services import project_store
from ui.backend.app.services.prompt_context.loaders import (
    character_cards,
    platform_market,
    worldbook,
)


def _fresh_db() -> str:
    db = os.path.join(tempfile.mkdtemp(), "store.db")
    con = sqlite3.connect(db)
    con.execute("PRAGMA foreign_keys=ON")
    ensure_creation_tables(con)
    con.execute("INSERT INTO projects (project_id, title) VALUES (?, ?)",
                ("p1", "test"))
    con.commit()
    con.close()
    return db


class TestPlatformMarketPlaceholder(unittest.TestCase):
    def test_returns_empty(self) -> None:
        self.assertEqual(platform_market.load("p1"), "")
        self.assertEqual(platform_market.load("p1", exclude={"platform"}), "")


class TestCharacterCardsSnapshot(unittest.TestCase):

    def setUp(self) -> None:
        self.db = _fresh_db()
        # Patch get_db_path so loader picks our temp DB.
        self._patcher = mock.patch(
            "ui.backend.app.services.project_paths.get_db_path",
            return_value=self.db,
        )
        self._patcher.start()

    def tearDown(self) -> None:
        self._patcher.stop()

    def _seed_character(self) -> None:
        project_store.upsert_character(self.db, {
            "project_id": "p1", "name": "李星河",
            "role": "主角",
            "appearance": "瘦削；眼神锐利",
            "personality": "沉稳寡言",
            "background": "考古世家",
            "speech_style": "言简意赅",
            "layer_b": {"loss_aversion": 1.5, "value_weights": {"survival": 0.4}},
            "dynamic_snapshots": [
                {
                    "chapter": "第3章",
                    "personality": "更加阴沉",
                    "layer_b": {"loss_aversion": 3.0, "impulse_probability": 0.5},
                    "hidden_identities": [
                        {"name": "陈墨", "revealed_to": ["李清漪"],
                         "notes": "为复仇隐姓埋名"},
                    ],
                },
                {
                    "chapter": "第8章",
                    "personality": "重新平静",
                    "layer_b": {"loss_aversion": 2.0},
                    "hidden_identities": [],
                },
            ],
        })

    def test_picks_latest_snapshot_when_no_chapter_num(self) -> None:
        self._seed_character()
        out = character_cards.load("p1", ["李星河"])
        # Last snapshot wins → personality "重新平静"
        self.assertIn("重新平静", out)
        self.assertNotIn("更加阴沉", out)
        self.assertIn("决策倾向", out)

    def test_picks_chapter_3_snapshot_when_chapter_num_is_5(self) -> None:
        self._seed_character()
        out = character_cards.load("p1", ["李星河"], chapter_num=5)
        # Snapshot 第3章 wins (latest ≤ 5)
        self.assertIn("更加阴沉", out)
        self.assertIn("化名「陈墨」", out)
        self.assertIn("已知真相：李清漪", out)

    def test_renders_base_layer_b_when_no_snapshot(self) -> None:
        project_store.upsert_character(self.db, {
            "project_id": "p1", "name": "无快照",
            "personality": "活泼",
            "layer_b": {"loss_aversion": 2.5, "value_weights": {"freedom": 0.6}},
        })
        out = character_cards.load("p1", ["无快照"])
        self.assertIn("活泼", out)
        self.assertIn("损失厌恶", out)
        self.assertIn("自由 60%", out)

    def test_exclude_filters_character(self) -> None:
        self._seed_character()
        out = character_cards.load("p1", ["李星河"], exclude={"李星河"})
        self.assertEqual(out, "")

    def test_unknown_name_skipped(self) -> None:
        out = character_cards.load("p1", ["不存在"])
        self.assertEqual(out, "")


class TestWorldbookLoader(unittest.TestCase):

    def setUp(self) -> None:
        self.db = _fresh_db()
        self._patcher = mock.patch(
            "ui.backend.app.services.project_paths.get_db_path",
            return_value=self.db,
        )
        self._patcher.start()
        # Seed worldbook entries with pre-computed embeddings so we
        # don't need to hit a real embedding provider.
        for title, content, vec in [
            ("星门", "高维位面之间的传送门", [1.0, 0.0, 0.0]),
            ("六道议会", "六个大陆的最高决策机构", [0.0, 1.0, 0.0]),
            ("黑曜宝石", "可短暂封印星门的稀有矿物", [0.9, 0.1, 0.0]),
        ]:
            saved = project_store.upsert_worldbook(self.db, {
                "project_id": "p1", "title": title, "content": content,
                "category": "杂项",
            })
            text = project_store.worldbook_embedding_text(saved)
            h = project_store._hash_embedding_text(text)
            project_store.set_worldbook_embedding(self.db, saved["id"], vec, h)

    def tearDown(self) -> None:
        self._patcher.stop()

    def test_returns_empty_without_chapter_id(self) -> None:
        self.assertEqual(worldbook.load("p1"), "")

    def test_returns_empty_without_synopsis(self) -> None:
        # chapter_id provided but editor doc has no chapter / synopsis.
        self.assertEqual(worldbook.load("p1", chapter_id="ch_missing"), "")

    def test_embedding_failure_falls_back_to_all_entries(self) -> None:
        # Seed an editor doc with a synopsis so the loader proceeds.
        project_store.save_editor_doc(self.db, "p1", {
            "volumes": [{"chapters": [{
                "id": "ch1", "synopsis": "主角找到黑曜宝石封印星门",
            }]}],
        })
        # Force embed_sync to fail → fall back to unranked render.
        with mock.patch.object(worldbook, "embed_sync", return_value=[]):
            out = worldbook.load("p1", chapter_id="ch1")
        self.assertIn("星门", out)
        self.assertIn("六道议会", out)
        self.assertIn("黑曜宝石", out)

    def test_ranks_by_cosine_similarity(self) -> None:
        project_store.save_editor_doc(self.db, "p1", {
            "volumes": [{"chapters": [{
                "id": "ch1", "synopsis": "传送门相关情节",
            }]}],
        })
        # Query embedding [1, 0, 0] → 星门 (1.0) > 黑曜宝石 (~0.99) > 六道议会 (0).
        with mock.patch.object(
            worldbook, "embed_sync",
            return_value=[[1.0, 0.0, 0.0]],  # only query, no stale entries
        ):
            out = worldbook.load("p1", chapter_id="ch1")
        idx_xingmen = out.index("星门")
        idx_baoshi = out.index("黑曜宝石")
        idx_yihui = out.index("六道议会")
        # 星门 most relevant, 六道议会 least.
        self.assertLess(idx_xingmen, idx_yihui)
        self.assertLess(idx_baoshi, idx_yihui)


if __name__ == "__main__":
    unittest.main()
