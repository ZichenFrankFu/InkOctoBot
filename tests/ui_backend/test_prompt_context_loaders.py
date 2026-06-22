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


class TestPlatformMarketDataDriven(unittest.TestCase):
    """Confirms the loader resolves the project's platform string against
    REAL stored data only — no hardcoded alias table — and pulls
    content from market-extractor outputs (category_aggregated_stats /
    compute_cache opening_nlp).

    Three fuzzy-matcher tiers:
    1. project="起点中文网", data="起点"  → stored ⊂ project match
    2. project="起点",       data="起点"  → exact match
    3. project="qidian",     data="起点"  → no overlap → loader skips
    """

    def setUp(self) -> None:
        import os, sqlite3, tempfile, time, json
        from storage.market_extractor_schema import ensure_market_extractor_tables
        from storage.project_schema import (
            ensure_creation_tables, _ensure_projects_market_columns,
        )
        from ui.backend.app.services import project_store as ps

        self._ps = ps
        self.db = os.path.join(tempfile.mkdtemp(), "pm.db")
        with sqlite3.connect(self.db) as con:
            ensure_creation_tables(con)
            _ensure_projects_market_columns(con)
            ensure_market_extractor_tables(con)
            con.execute(
                "INSERT INTO category_aggregated_stats(stats_id, platform, "
                "category, source_works_count, "
                "opening_hook_type_distribution_json, "
                "chapter_word_count_stats_json) "
                "VALUES('cas_1', '起点', '玄幻', 10, ?, ?)",
                (json.dumps({"金手指开场": 5, "高潮开场": 3}, ensure_ascii=False),
                 json.dumps({"mean": 3200, "p25": 2800, "p50": 3200,
                              "p75": 3600}, ensure_ascii=False)))
            con.execute(
                "CREATE TABLE IF NOT EXISTS compute_cache ("
                "cache_key TEXT PRIMARY KEY, payload_json TEXT NOT NULL, "
                "version_key TEXT NOT NULL DEFAULT '', updated_at REAL NOT NULL)"
            )
            con.execute(
                "INSERT INTO compute_cache VALUES('opening_nlp:起点', ?, '', ?)",
                (json.dumps({
                    "available": True, "sample_count": 50, "unique_novels": 20,
                    "word_count_summary": {"mean": 2900, "min": 1500, "max": 4500},
                    "dialogue_ratio": {"mean": 0.28},
                }, ensure_ascii=False), time.time()))
            con.commit()
        self._patcher = mock.patch(
            "ui.backend.app.services.project_paths.get_db_path",
            return_value=self.db,
        )
        self._patcher.start()

    def tearDown(self) -> None:
        self._patcher.stop()

    def test_label_matches_stored_short(self) -> None:
        """project saved with full label still finds the short-form data."""
        self._ps.upsert_project(self.db, {
            "id": "p1", "name": "a", "platform": "起点中文网", "category": "玄幻",
        })
        out = platform_market.load("p1")
        self.assertIn("平台风格基线", out)
        self.assertIn("金手指开场", out)
        self.assertIn("开篇字数", out)

    def test_exact_match(self) -> None:
        self._ps.upsert_project(self.db, {
            "id": "p2", "name": "b", "platform": "起点", "category": "玄幻",
        })
        out = platform_market.load("p2")
        self.assertIn("平台风格基线", out)

    def test_no_overlap_returns_empty(self) -> None:
        """No hardcoded alias table — a project value with zero overlap
        against any stored identifier MUST skip the loader."""
        self._ps.upsert_project(self.db, {
            "id": "p3", "name": "c", "platform": "qidian", "category": "玄幻",
        })
        self.assertEqual(platform_market.load("p3"), "")

    def test_no_platform_set(self) -> None:
        self._ps.upsert_project(self.db, {"id": "p4", "name": "d"})
        self.assertEqual(platform_market.load("p4"), "")


class TestCharacterCardsLoaderBaseline(unittest.TestCase):
    """Smoke tests for the rewritten loader — stable path only.

    The full 4-transition-state coverage lives in
    ``test_loader_character_cards_v2.py``; this class just exercises
    the no-snapshot baseline so the legacy file keeps its top-level
    smoke check.
    """

    def setUp(self) -> None:
        self.db = _fresh_db()
        self._patcher = mock.patch(
            "ui.backend.app.services.project_paths.get_db_path",
            return_value=self.db,
        )
        self._patcher.start()

    def tearDown(self) -> None:
        self._patcher.stop()

    def test_renders_base_card_without_snapshots(self) -> None:
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
        project_store.upsert_character(self.db, {
            "project_id": "p1", "name": "X", "personality": "x",
        })
        out = character_cards.load("p1", ["X"], exclude={"X"})
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
