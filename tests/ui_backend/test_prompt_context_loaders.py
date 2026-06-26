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
    content from market-extractor outputs.

    Per the merged design (no separate ``market_overview`` block) the
    body is composed of:
      · 高级特征 — six subsections drawn from a ``platform_profiles`` row
        (profile_summary / style_baseline / pacing_guidance /
        signature_devices_description / neologism_step2 / style_dimensions).
      · 基础特征 — ``compute_cache[opening_nlp:<platform>]['spec_stats']``
        rendered via ``opening_stats.render_stats_for_prompt``.

    Three fuzzy-matcher tiers (project label → stored identifier):
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
            # Active platform_profiles row — drives the six 高级特征 subsections.
            con.execute(
                "INSERT INTO platform_profiles("
                "profile_id, platform, category, profile_version, "
                "profile_summary, style_baseline, "
                "signature_devices_description, pacing_guidance, "
                "style_dimensions_json, neologism_step2_json, "
                "loader_payload, confidence_label, "
                "extraction_started_at, extraction_completed_at) "
                "VALUES('prof_1', '起点', '玄幻', 1, ?, ?, ?, ?, ?, ?, '', "
                "'manual', ?, ?)",
                (
                    "起点玄幻的代表画像：金手指主导、爽点密集、首章 3000 字开篇即设悬念。",
                    json.dumps({
                        "narration_pov": "第三人称限知", "tone": "热血",
                        "language_register": "口语化", "sentence_rhythm": "短句为主",
                    }, ensure_ascii=False),
                    "招牌叙事手法：装逼打脸 / 系统升级 / 反派智商在线。",
                    json.dumps({
                        "first_chapter_words": "2500-3500",
                        "info_release_strategy": "渐进揭露",
                    }, ensure_ascii=False),
                    json.dumps({
                        "A_protagonist": {
                            "A1_appearance": "首句登场，动作场景切入。",
                            "A3_cheat": "系统流，初章揭示。",
                        },
                        "B_social": {
                            "B1_network": "前5章登场：师父 / 师姐 / 反派A — 应被剔除",
                            "B2_ensemble": "单主角聚焦，反派第3章登场。",
                        },
                        "E_style": {
                            "E1_writing_style": "热血爽快，带轻度吐槽。",
                        },
                    }, ensure_ascii=False),
                    json.dumps({
                        "proper_nouns": ["灵根", "宗门", "玄气"],
                        "person_names": ["林萧"],
                        "naming_patterns": "单字+宗/门，叠音古风双字。",
                    }, ensure_ascii=False),
                    time.time(), time.time(),
                ),
            )
            # opening_nlp cache — drives 基础特征 via render_stats_for_prompt.
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
                    "spec_stats": {
                        "available": True,
                        "chapters_analyzed": 120,
                        "first_chapter_words_avg": 2950,
                        "chapter_words_avg": 2900,
                        "chapter_words_median": 2850,
                        "avg_sentence_length": 22.4,
                        "punctuation_density_per_1k": {"。": 35, "，": 88},
                        "linguistic_features": {
                            "pos_distribution": {
                                "available": True,
                                "action_scene": 0.22,
                                "description_density": 0.18,
                                "setting_density": 0.28,
                            },
                            "sentiment": {
                                "available": True,
                                "emotion_ratio": {"乐": 0.32, "怒": 0.18, "惧": 0.12},
                            },
                        },
                        "top_words": [
                            {"word": "灵气", "count": 18, "relative_freq_permille": 5.2},
                            {"word": "宗门", "count": 14, "relative_freq_permille": 4.0},
                        ],
                    },
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
        # 高级特征 subsections present.
        self.assertIn("平台综述", out)
        self.assertIn("风格基线", out)
        self.assertIn("招牌叙事手法", out)
        self.assertIn("专有名词", out)
        self.assertIn("灵根", out)
        # 基础特征 rendered via render_stats_for_prompt.
        self.assertIn("基础特征", out)
        self.assertIn("词性分布", out)
        # B1_network deliberately dropped — must NOT appear in prompt.
        self.assertNotIn("应被剔除", out)
        # 市场基底 / 市场趋势 sections removed entirely.
        self.assertNotIn("市场数据库", out)
        self.assertNotIn("市场趋势", out)

    def test_exact_match(self) -> None:
        self._ps.upsert_project(self.db, {
            "id": "p2", "name": "b", "platform": "起点", "category": "玄幻",
        })
        out = platform_market.load("p2")
        self.assertIn("平台风格基线", out)
        self.assertIn("平台综述", out)

    def test_no_overlap_returns_empty(self) -> None:
        """A project value with zero overlap against any stored
        identifier — AND not bridged by ``platform_aliases`` — must skip
        the loader. Use a fake platform name that the alias map doesn't
        know about (``"未识别平台"``) so the bridge can't rescue it."""
        self._ps.upsert_project(self.db, {
            "id": "p3", "name": "c", "platform": "未识别平台", "category": "玄幻",
        })
        self.assertEqual(platform_market.load("p3"), "")

    def test_no_platform_set(self) -> None:
        self._ps.upsert_project(self.db, {"id": "p4", "name": "d"})
        self.assertEqual(platform_market.load("p4"), "")


class TestPlatformMarketCanonicalAliasBridge(unittest.TestCase):
    """Project DB stores the Chinese display label (起点中文网 / 番茄小说)
    while the crawler stores the English slug (qidian / fanqie). The
    purely substring-based matcher couldn't bridge them — the alias map
    in ``ui.backend.app.services.platform_aliases`` does."""

    def setUp(self) -> None:
        import os, sqlite3, tempfile, time, json
        from storage.project_schema import (
            ensure_creation_tables, _ensure_projects_market_columns,
        )
        from storage.market_extractor_schema import ensure_market_extractor_tables
        from ui.backend.app.services import project_store as ps
        self._ps = ps
        tmp = tempfile.mkdtemp()
        self.project_db = os.path.join(tmp, "p.db")
        self.crawler_db = os.path.join(tmp, "c.db")
        with sqlite3.connect(self.project_db) as con:
            ensure_creation_tables(con)
            _ensure_projects_market_columns(con)
            ensure_market_extractor_tables(con)
            con.execute(
                "INSERT INTO platform_profiles(profile_id, platform, "
                "category, profile_version, profile_summary, "
                "confidence_label, extraction_started_at, "
                "extraction_completed_at) "
                "VALUES('pp_q', 'qidian', '', 1, '起点平台综述。', "
                "'manual', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        # Crawler DB writes English slugs.
        with sqlite3.connect(self.crawler_db) as con:
            con.execute(
                "CREATE TABLE novels (novel_uid INTEGER PRIMARY KEY, "
                "platform TEXT)"
            )
            con.execute("INSERT INTO novels VALUES (1, 'qidian')")
        self._patcher_db = mock.patch(
            "ui.backend.app.services.project_paths.get_db_path",
            return_value=self.project_db,
        )
        self._patcher_db.start()
        self._patcher_crawler = mock.patch(
            "ui.backend.app.utils.resolve_crawler_db_path",
            return_value=self.crawler_db,
        )
        self._patcher_crawler.start()

    def tearDown(self) -> None:
        self._patcher_db.stop()
        self._patcher_crawler.stop()

    def test_chinese_label_bridges_to_english_slug(self) -> None:
        """Project saved with long-form Chinese label still resolves to
        the crawler's English slug via the canonical alias map."""
        self._ps.upsert_project(self.project_db, {
            "id": "p1", "name": "demo",
            "platform": "起点中文网", "category": "星际文明",
        })
        out = platform_market.load("p1")
        self.assertIn("平台风格基线", out)
        self.assertIn("起点平台综述", out)


class TestPlatformMarketLegacyFallbacks(unittest.TestCase):
    """Two graceful-degradation paths that protect existing projects from
    regressing to 未注入 after the loader merger:

    1. ``platform_profiles`` row with ONLY ``loader_payload`` populated
       (none of the six structured fields) — older extractions where the
       LLM filled the writing baseline paragraph but not the per-field
       breakdown. The loader surfaces it as one "整段画像" subsection.
    2. ``opening_nlp`` cache row WITHOUT ``spec_stats`` — pre-spec_stats
       cache shape. The loader renders the legacy fields
       (word_count_summary / dialogue_ratio / first_sentence_types …)
       instead of returning empty.
    """

    def setUp(self) -> None:
        import os, sqlite3, tempfile, time, json
        from storage.market_extractor_schema import ensure_market_extractor_tables
        from storage.project_schema import (
            ensure_creation_tables, _ensure_projects_market_columns,
        )
        from ui.backend.app.services import project_store as ps
        self._ps = ps
        self.db = os.path.join(tempfile.mkdtemp(), "legacy.db")
        with sqlite3.connect(self.db) as con:
            ensure_creation_tables(con)
            _ensure_projects_market_columns(con)
            ensure_market_extractor_tables(con)
            con.execute(
                "CREATE TABLE IF NOT EXISTS compute_cache ("
                "cache_key TEXT PRIMARY KEY, payload_json TEXT NOT NULL, "
                "version_key TEXT NOT NULL DEFAULT '', updated_at REAL NOT NULL)"
            )
            # platform_profiles row with ONLY loader_payload (legacy shape).
            con.execute(
                "INSERT INTO platform_profiles("
                "profile_id, platform, category, profile_version, "
                "loader_payload, confidence_label, "
                "extraction_started_at, extraction_completed_at) "
                "VALUES('prof_legacy', '起点', '玄幻', 1, ?, 'high', ?, ?)",
                (
                    "起点玄幻：穿越流为主，短句节奏，前期密集打脸 — 写作时严格遵循以上风格基线。",
                    time.time(), time.time(),
                ),
            )
            # opening_nlp cache row WITHOUT spec_stats (legacy shape).
            con.execute(
                "INSERT INTO compute_cache VALUES(?, ?, '', ?)",
                ("opening_nlp:起点", json.dumps({
                    "available": True, "sample_count": 50, "unique_novels": 20,
                    "word_count_summary": {"mean": 2900, "min": 1500, "max": 4500},
                    "dialogue_ratio": {"mean": 0.28},
                    "sentence_length": {"mean": 22.4},
                    "first_sentence_types": [
                        {"label": "动作", "count": 18},
                        {"label": "对话", "count": 12},
                    ],
                    "end_hook_types": [
                        {"label": "悬念", "count": 22},
                        {"label": "反转", "count": 8},
                    ],
                }, ensure_ascii=False), time.time()))
            con.commit()
        self._patcher = mock.patch(
            "ui.backend.app.services.project_paths.get_db_path",
            return_value=self.db,
        )
        self._patcher.start()

    def tearDown(self) -> None:
        self._patcher.stop()

    def test_loader_payload_only_profile_falls_back_to_整段画像(self) -> None:
        """Even when the six structured fields are blank, loader_payload
        must reach the prompt — projects with older extractions can't
        regress to 未注入 silently."""
        self._ps.upsert_project(self.db, {
            "id": "p_leg", "name": "demo",
            "platform": "起点", "category": "玄幻",
        })
        out = platform_market.load("p_leg")
        self.assertIn("平台风格基线", out)
        self.assertIn("整段画像", out)
        self.assertIn("穿越流为主", out)

    def test_legacy_opening_nlp_cache_still_renders(self) -> None:
        """A cache row without ``spec_stats`` (older NLP schema) still
        injects the basic features via the legacy renderer."""
        self._ps.upsert_project(self.db, {
            "id": "p_leg", "name": "demo",
            "platform": "起点", "category": "玄幻",
        })
        out = platform_market.load("p_leg")
        self.assertIn("基础特征", out)
        self.assertIn("开篇字数", out)
        self.assertIn("首句类型", out)
        self.assertIn("动作", out)


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
