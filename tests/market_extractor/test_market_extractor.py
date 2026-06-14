"""Consolidated tests for the market extractor (Phases 0-6).

LLM-calling pieces (llm_extractor / neologism_extractor LLM step /
profile_synthesizer / job_runner phase 4) are mocked. Tests focus on
DB shape, NLP heuristics, aggregator math, and Loader 2 wiring.
"""
from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from storage.project_schema import ensure_creation_tables
from ui.backend.app.services.market_extractor import (
    category_aggregator, chapter_fetcher, dictionaries,
    job_runner, llm_extractor, neologism_extractor, nlp_features,
    profile_evaluator, profile_synthesizer,
    representative_selector, work_aggregator,
)


def _fresh_db() -> str:
    db = os.path.join(tempfile.mkdtemp(), "mx.db")
    con = sqlite3.connect(db)
    ensure_creation_tables(con)
    con.commit()
    con.close()
    return db


def _seed_crawler_db() -> str:
    """Create a minimal stand-in crawler DB so the selector has data."""
    p = os.path.join(tempfile.mkdtemp(), "Crawler.db")
    with sqlite3.connect(p) as con:
        con.execute(
            "CREATE TABLE novels ("
            "  novel_id TEXT PRIMARY KEY, title TEXT, "
            "  platform TEXT, category TEXT, "
            "  collection_count INTEGER, comment_count INTEGER, "
            "  monthly_ticket INTEGER, word_count INTEGER, "
            "  last_updated_at TEXT)"
        )
        con.execute(
            "CREATE TABLE chapters ("
            "  novel_id TEXT, chapter_num INTEGER, content TEXT, "
            "  PRIMARY KEY(novel_id, chapter_num))"
        )
        # 10 candidate novels for "qidian / xianxia".
        for i in range(10):
            con.execute(
                "INSERT INTO novels VALUES (?, ?, 'qidian', 'xianxia', "
                "?, ?, ?, ?, '2025-01-01')",
                (f"n{i}", f"小说{i}", 100 + i * 50, 30 + i * 5,
                 10 + i, 250_000 + i * 10_000),
            )
        for nid in (f"n{i}" for i in range(10)):
            for k in range(1, 4):
                con.execute(
                    "INSERT INTO chapters VALUES (?, ?, ?)",
                    (nid, k,
                     "主角进入了幽冥谷。\n\n他抬头望天，灵气澎湃。\n\n"
                     "求收藏求推荐！\n\n这是序列魔药。"),
                )
        con.commit()
    return p


# ─────────── dictionaries ───────────


class TestDictionaries(unittest.TestCase):

    def test_xianxia_loads_seed_words(self) -> None:
        words = dictionaries.load_genre_dict("xianxia")
        self.assertIn("灵气", words)
        self.assertGreater(len(words), 50)

    def test_unknown_genre_returns_empty(self) -> None:
        self.assertEqual(dictionaries.load_genre_dict("nonexistent"), frozenset())

    def test_classical_words_loaded(self) -> None:
        words = dictionaries.load_classical_words()
        self.assertIn("吾", words)

    def test_append_dedupes(self) -> None:
        # Use a throwaway category to avoid polluting seed dicts.
        cat = f"throwaway_{os.getpid()}"
        n1 = dictionaries.append_to_genre_dict(cat, ["test_word_a", "test_word_b"])
        n2 = dictionaries.append_to_genre_dict(cat, ["test_word_a", "test_word_c"])
        self.assertEqual(n1, 2)
        self.assertEqual(n2, 1)
        # Clean up the file so we don't leave litter.
        from pathlib import Path
        p = (
            Path(__file__).resolve().parents[2]
            / "ui/backend/app/services/market_extractor/resources"
            / "dictionaries/genre" / f"{cat}.txt"
        )
        if p.exists():
            p.unlink()


# ─────────── chapter cleaner ───────────


class TestChapterCleaner(unittest.TestCase):

    def test_strips_recommendation_lines(self) -> None:
        raw = "主角看了一眼。\n求收藏求推荐！\n他笑了笑。"
        out = chapter_fetcher.clean_chapter_text(raw)
        self.assertNotIn("求收藏", out)
        self.assertIn("主角看了一眼", out)
        self.assertIn("他笑了笑", out)

    def test_collapses_blank_lines(self) -> None:
        raw = "段一\n\n\n\n段二"
        out = chapter_fetcher.clean_chapter_text(raw)
        self.assertEqual(out.count("\n\n"), 1)

    def test_empty_input_returns_empty(self) -> None:
        self.assertEqual(chapter_fetcher.clean_chapter_text(""), "")


# ─────────── representative selector ───────────


class TestRepresentativeSelector(unittest.TestCase):

    def setUp(self) -> None:
        self.db = _fresh_db()
        self.crawler = _seed_crawler_db()

    def test_select_top_works_writes_pool_rows(self) -> None:
        result = representative_selector.select(
            self.db, "qidian", "xianxia",
            top_k=5, crawler_db=self.crawler, seed=42,
        )
        self.assertGreater(result["selected"], 0)
        # Some are tagged holdout (at least 1 with 10% fraction).
        self.assertGreaterEqual(result["holdout"], 1)
        rows = representative_selector.list_selected(
            self.db, "qidian", "xianxia", include_holdout=True,
        )
        self.assertEqual(len(rows), result["selected"])

    def test_missing_crawler_db_returns_zero(self) -> None:
        result = representative_selector.select(
            self.db, "qidian", "xianxia",
            crawler_db="/does/not/exist.db",
        )
        self.assertEqual(result["selected"], 0)
        self.assertEqual(result["work_ids"], [])

    def test_exclude_work(self) -> None:
        representative_selector.select(
            self.db, "qidian", "xianxia",
            top_k=3, crawler_db=self.crawler,
        )
        rows = representative_selector.list_selected(
            self.db, "qidian", "xianxia", include_holdout=True,
        )
        ok = representative_selector.exclude_work(self.db, rows[0]["work_id"])
        self.assertTrue(ok)
        after = representative_selector.list_selected(
            self.db, "qidian", "xianxia", include_holdout=True,
        )
        self.assertEqual(len(after), len(rows) - 1)


# ─────────── NLP features ───────────


class TestNLPFeatures(unittest.TestCase):

    def test_basic_counts(self) -> None:
        text = "他望了望天空。\n\n灵气扑面而来！"
        out = nlp_features.extract(text)
        self.assertGreater(out["word_count"], 0)
        self.assertEqual(out["paragraph_count"], 2)

    def test_dialogue_detection(self) -> None:
        text = "“你是谁？”他问道。\n\n“我是主角。”\n\n描述段。"
        out = nlp_features.extract(text)
        self.assertGreater(out["dialogue_ratio"], 0)
        self.assertGreater(out["dialogue_paragraph_ratio"], 0)

    def test_top_50_words_is_json(self) -> None:
        out = nlp_features.extract("一二三四五" * 10)
        parsed = json.loads(out["top_50_words_json"])
        self.assertIsInstance(parsed, list)


# ─────────── LLM extractor ───────────


class TestLLMExtractor(unittest.IsolatedAsyncioTestCase):

    async def test_flatten_handles_missing_keys(self) -> None:
        """Missing nested keys → None or 0 (never crash)."""
        cols = llm_extractor.flatten_to_columns({})
        self.assertIsNone(cols["protagonist_strength_position"])
        self.assertEqual(cols["character_count_in_5ch"], 0)
        self.assertEqual(cols["has_early_face_slap"], 0)

    async def test_flatten_full_payload(self) -> None:
        parsed = {
            "A2_protagonist_profile": {
                "strength_position": "弱势",
                "personality_keywords": ["冷酷", "倔强"],
            },
            "D1_opening_hook": {"type": "穿越", "description": "重生在异世"},
            "E2_emotional_tone": {"dominant": "复仇", "volatility": "高"},
        }
        cols = llm_extractor.flatten_to_columns(parsed)
        self.assertEqual(cols["protagonist_strength_position"], "弱势")
        self.assertIn("冷酷", cols["protagonist_personality_json"])
        self.assertEqual(cols["opening_hook_type"], "穿越")
        self.assertEqual(cols["emotional_tone"], "复仇")

    def test_extract_json_tolerates_code_fence(self) -> None:
        raw = '```json\n{"a": 1}\n```'
        out = llm_extractor._extract_json(raw)
        self.assertEqual(out, {"a": 1})


# ─────────── neologism extractor ───────────


class TestNeologismExtractor(unittest.TestCase):

    def test_recall_finds_repeated_terms(self) -> None:
        chapters = {
            1: "序列魔药是一种神秘的药剂。鲁恩王国的人都知道。",
            2: "他服下了序列魔药，立刻感受到鲁恩王国的力量。",
        }
        cands = neologism_extractor.recall_candidates(chapters)
        terms = set(cands.keys())
        self.assertTrue(any("魔药" in t for t in terms))

    def test_genre_dict_filter(self) -> None:
        """A xianxia word like '灵气' should get filtered out (it's in
        the genre dict, not a work-specific neologism)."""
        cands = {
            "灵气":     {"frequency": 5, "first_chapter": 1, "contexts": []},
            "序列魔药": {"frequency": 4, "first_chapter": 1, "contexts": []},
        }
        filtered = neologism_extractor.filter_by_genre_dict(cands, "xianxia")
        self.assertNotIn("灵气", filtered)
        self.assertIn("序列魔药", filtered)


class TestNeologismIsolation(unittest.TestCase):
    """Spec § 七 ★: work_neologisms must be strictly per-work — different
    works' neologisms never share a row."""

    def test_per_work_storage(self) -> None:
        db = _fresh_db()
        # Insert directly to bypass the LLM-classification path.
        with sqlite3.connect(db) as con:
            for wid, term in [("rw_aaa", "序列魔药"), ("rw_bbb", "序列魔药")]:
                con.execute(
                    "INSERT INTO work_neologisms "
                    "(neologism_id, work_id, term, neologism_type) "
                    "VALUES (?, ?, ?, 'item')",
                    (f"wn_{wid}", wid, term),
                )
            con.commit()
            rows = con.execute(
                "SELECT work_id FROM work_neologisms WHERE term = '序列魔药'"
            ).fetchall()
        # Two separate rows, one per work — no merging across works.
        self.assertEqual(len(rows), 2)
        self.assertEqual({r[0] for r in rows}, {"rw_aaa", "rw_bbb"})


# ─────────── work + category aggregators ───────────


class TestWorkAggregator(unittest.TestCase):

    def setUp(self) -> None:
        self.db = _fresh_db()
        with sqlite3.connect(self.db) as con:
            con.execute(
                "INSERT INTO representative_works_pool "
                "(work_id, platform, category) VALUES "
                "('rw_x', 'qidian', 'xianxia')"
            )
            for k in (1, 2, 3):
                con.execute(
                    "INSERT INTO chapter_features "
                    "(feature_id, work_id, chapter_num, word_count, "
                    " dialogue_ratio, emotional_tone, opening_hook_type, "
                    " antagonist_first_chapter) "
                    "VALUES (?, 'rw_x', ?, ?, ?, ?, ?, ?)",
                    (f"cf_{k}", k, 1000 + k * 100, 0.2 + k * 0.1,
                     "复仇" if k != 2 else "希望",
                     "穿越" if k == 1 else "",
                     3 if k == 1 else 0),
                )
            con.commit()

    def test_aggregate_writes_row(self) -> None:
        payload = work_aggregator.aggregate(self.db, "rw_x")
        self.assertEqual(payload["work_id"], "rw_x")
        self.assertGreater(payload["avg_chapter_word_count"], 1000)
        self.assertEqual(payload["dominant_emotional_tone"], "复仇")
        self.assertEqual(payload["opening_hook_type"], "穿越")


class TestCategoryAggregator(unittest.TestCase):

    def setUp(self) -> None:
        self.db = _fresh_db()
        with sqlite3.connect(self.db) as con:
            for wid in ("rw_a", "rw_b"):
                con.execute(
                    "INSERT INTO representative_works_pool "
                    "(work_id, platform, category) VALUES (?, 'qidian', 'xianxia')",
                    (wid,),
                )
                con.execute(
                    "INSERT INTO chapter_features "
                    "(feature_id, work_id, chapter_num, word_count, "
                    " dialogue_ratio, opening_hook_type, emotional_tone) "
                    "VALUES (?, ?, 1, 2000, 0.3, '穿越', '希望')",
                    (f"cf_{wid}", wid),
                )
                con.execute(
                    "INSERT INTO work_aggregated_features "
                    "(work_id, platform, category, neologism_count, "
                    " node_positions_json, work_top_words_json, "
                    " neologism_count_by_type_json) "
                    "VALUES (?, 'qidian', 'xianxia', 5, '{}', '[]', '{}')",
                    (wid,),
                )
            con.commit()

    def test_aggregate_writes_row(self) -> None:
        payload = category_aggregator.aggregate(
            self.db, "qidian", "xianxia", update_genre_dict=False,
        )
        self.assertEqual(payload["source_works_count"], 2)
        opening = json.loads(payload["opening_hook_type_distribution_json"])
        self.assertEqual(opening.get("穿越"), 2)


# ─────────── profile synthesizer ───────────


class TestProfileSynthesizer(unittest.IsolatedAsyncioTestCase):

    async def test_synthesizes_via_mocked_llm(self) -> None:
        db = _fresh_db()
        # Seed an aggregated stats row.
        with sqlite3.connect(db) as con:
            con.execute(
                "INSERT INTO category_aggregated_stats "
                "(stats_id, platform, category, source_works_count, "
                " source_works_ids_json) "
                "VALUES ('cas_x', 'qidian', 'xianxia', 5, '[\"rw_1\",\"rw_2\"]')"
            )
            con.commit()

        fake_profile = {
            "profile_summary": "起点-玄幻平台特性总结",
            "recommended_openings": [{"name": "穿越流", "structure": "...",
                                        "example_template": "..."}],
            "style_baseline": {"sentence_style": "短句"},
            "signature_devices_description": "金手指 + 打脸",
            "pacing_guidance": {"first_chapter": "开篇即冲突"},
            "loader_payload": "起点玄幻：穿越/重生开局，短句快节奏，10章内必打脸。",
        }

        async def fake_call(stats, platform, category, **_kw):
            return fake_profile, "mock/m"

        with mock.patch.object(
            profile_synthesizer, "_call_synthesis_llm", fake_call,
        ):
            result = await profile_synthesizer.synthesize(
                db, "qidian", "xianxia",
            )

        self.assertTrue(result["profile_id"].startswith("pp_"))
        # Profile row exists + payload < 1000 chars.
        with sqlite3.connect(db) as con:
            row = con.execute(
                "SELECT loader_payload, confidence_label FROM platform_profiles "
                "WHERE profile_id = ?", (result["profile_id"],),
            ).fetchone()
        self.assertIn("穿越", row[0])
        self.assertLessEqual(len(row[0]), 1000)

    async def test_strips_naming_suggestions(self) -> None:
        """Spec § 七 ★: any naming-pattern phrase in the LLM output
        gets stripped before persistence."""
        db = _fresh_db()
        with sqlite3.connect(db) as con:
            con.execute(
                "INSERT INTO category_aggregated_stats "
                "(stats_id, platform, category, source_works_count, "
                " source_works_ids_json) "
                "VALUES ('cas_y', 'qidian', 'xianxia', 1, '[]')"
            )
            con.commit()

        contaminated = {
            "profile_summary": "测试",
            "recommended_openings": [],
            "style_baseline": {},
            "signature_devices_description": "金手指。命名建议：用'X决'格式。打脸。",
            "pacing_guidance": {},
            "loader_payload": "起点玄幻特性。命名模式：N+王国/N+教派。短句节奏。",
        }

        async def fake_call(stats, platform, category, **_kw):
            return contaminated, "mock/m"

        with mock.patch.object(
            profile_synthesizer, "_call_synthesis_llm", fake_call,
        ):
            result = await profile_synthesizer.synthesize(
                db, "qidian", "xianxia",
            )
        with sqlite3.connect(db) as con:
            row = con.execute(
                "SELECT loader_payload, signature_devices_description "
                "FROM platform_profiles WHERE profile_id = ?",
                (result["profile_id"],),
            ).fetchone()
        self.assertNotIn("命名", row[0])
        self.assertNotIn("命名", row[1])


# ─────────── profile evaluator ───────────


class TestProfileEvaluator(unittest.TestCase):

    def test_evaluate_updates_score(self) -> None:
        db = _fresh_db()
        with sqlite3.connect(db) as con:
            con.execute(
                "INSERT INTO representative_works_pool "
                "(work_id, platform, category, is_holdout) VALUES "
                "('rw_h', 'qidian', 'xianxia', 1)"
            )
            con.execute(
                "INSERT INTO chapter_features "
                "(feature_id, work_id, chapter_num, dialogue_ratio, "
                " avg_sentence_length, idiom_density) "
                "VALUES ('cf_h1', 'rw_h', 1, 0.3, 15, 0.05)"
            )
            con.execute(
                "INSERT INTO chapter_features "
                "(feature_id, work_id, chapter_num, dialogue_ratio, "
                " avg_sentence_length, idiom_density) "
                "VALUES ('cf_s1', 'rw_s', 1, 0.3, 15, 0.05)"
            )
            con.execute(
                "INSERT INTO platform_profiles "
                "(profile_id, platform, category, source_works_ids_json) "
                "VALUES ('pp_x', 'qidian', 'xianxia', '[\"rw_s\"]')"
            )
            con.commit()
        result = profile_evaluator.evaluate(db, "pp_x")
        self.assertGreaterEqual(result["holdout_similarity"], 0.0)
        self.assertIn(result["confidence"], ("high", "medium", "low"))


# ─────────── Loader 2 wiring ───────────


class TestPlatformMarketLoader(unittest.TestCase):

    def test_no_profile_returns_none(self) -> None:
        from ui.backend.app.services.prompt_context.loaders import platform_market
        db = _fresh_db()
        with sqlite3.connect(db) as con:
            con.execute(
                "INSERT INTO projects (project_id, title) "
                "VALUES ('p1', 't')"
            )
            con.commit()
        with mock.patch(
            "ui.backend.app.services.project_paths.get_db_path",
            return_value=db,
        ):
            self.assertIsNone(platform_market.plan("p1"))

    def test_active_profile_returns_plan(self) -> None:
        from ui.backend.app.services.prompt_context.loaders import platform_market
        db = _fresh_db()
        with sqlite3.connect(db) as con:
            # Need projects.platform column — the legacy schema doesn't
            # have it. Verify via the tolerant column lookup.
            cols = {r[1] for r in con.execute("PRAGMA table_info(projects)")}
            self.assertIn("project_id", cols)
            # If platform isn't a column the loader degrades gracefully —
            # tests that depend on it should be skipped.
            if "platform" not in cols:
                self.skipTest("projects.platform column not in schema")
            con.execute(
                "INSERT INTO projects (project_id, title, platform, category) "
                "VALUES ('p1', 't', 'qidian', 'xianxia')"
            )
            con.execute(
                "INSERT INTO platform_profiles "
                "(profile_id, platform, category, profile_version, "
                " loader_payload, confidence_label) "
                "VALUES ('pp_x', 'qidian', 'xianxia', 1, "
                " '起点玄幻：穿越流为主，短句节奏。', 'high')"
            )
            con.commit()
        with mock.patch(
            "ui.backend.app.services.project_paths.get_db_path",
            return_value=db,
        ):
            p = platform_market.plan("p1")
        self.assertIsNotNone(p)
        rendered = p.render(p.target)
        self.assertIn("起点玄幻", rendered)


# ─────────── job runner ───────────


class TestJobRunner(unittest.IsolatedAsyncioTestCase):

    async def test_full_pipeline_with_mocked_llm(self) -> None:
        """End-to-end smoke: real selector + real NLP + mocked LLM."""
        db = _fresh_db()
        crawler = _seed_crawler_db()

        async def fake_chapter_llm(text, num, *, is_first_chapter, **_kw):
            return ({
                "A1_protagonist_first_appearance": {"chapter": num},
                "D1_opening_hook": {"type": "穿越"},
            }, "mock/m")

        async def fake_neologism_llm(cands, cat, **_kw):
            return ([], "mock/m")

        async def fake_synth(stats, platform, category, **_kw):
            return ({
                "profile_summary": "测试 profile",
                "recommended_openings": [],
                "style_baseline": {},
                "signature_devices_description": "",
                "pacing_guidance": {},
                "loader_payload": "测试 loader payload",
            }, "mock/m")

        with mock.patch.object(
            llm_extractor, "call_llm_for_chapter", fake_chapter_llm,
        ), mock.patch.object(
            neologism_extractor, "classify_with_llm", fake_neologism_llm,
        ), mock.patch.object(
            profile_synthesizer, "_call_synthesis_llm", fake_synth,
        ):
            job_id = await job_runner.run_job_async(
                db, "qidian", "xianxia", crawler_db=crawler,
            )
        job = job_runner.get_job_status(db, job_id)
        self.assertEqual(job["state"], "completed")
        self.assertIsNotNone(job["created_profile_id"])


class TestManualSubmit(unittest.TestCase):
    """整平台手动提交（category 留空）— 修复 'platform + category + response_raw
    required' 调用失败。"""

    def _client(self, db):
        from fastapi.testclient import TestClient
        from ui.backend.app.main import app
        return TestClient(app)

    def test_empty_category_accepted(self) -> None:
        db = _fresh_db()
        with mock.patch(
            "ui.backend.app.services.project_paths.get_db_path",
            return_value=db,
        ):
            r = self._client(db).post(
                "/api/market-extractor/manual-submit",
                json={"platform": "qidian", "category": "",
                      "response_raw": '{"loader_payload":"起点玄幻：短句节奏。"}'},
            )
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["category"], "")

    def test_missing_platform_or_raw_still_400(self) -> None:
        db = _fresh_db()
        with mock.patch(
            "ui.backend.app.services.project_paths.get_db_path",
            return_value=db,
        ):
            c = self._client(db)
            self.assertEqual(c.post("/api/market-extractor/manual-submit",
                                    json={"platform": "", "response_raw": "x"}).status_code, 400)
            self.assertEqual(c.post("/api/market-extractor/manual-submit",
                                    json={"platform": "qidian", "response_raw": ""}).status_code, 400)


if __name__ == "__main__":
    unittest.main()
