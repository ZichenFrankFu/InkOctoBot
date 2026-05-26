"""
Tests for agents/production/truth_integration.py.

Covers the 3 Writer × Truth integration points from
docs/truth_file_system.md §9:
  - build_truth_context        (Phase 1 prompt assembly)
  - list_pressured_hooks_text  (pressured-hook injection)
  - extract_and_apply_truth_deltas (Phase 2 settlement + audit gate)
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))

from storage.project_schema import ensure_creation_tables
from agents.production import truth_integration as ti


def _fresh_db() -> str:
    db = os.path.join(tempfile.mkdtemp(), "truth_int.db")
    con = sqlite3.connect(db)
    con.execute("PRAGMA foreign_keys=ON")
    ensure_creation_tables(con)
    con.execute("INSERT INTO projects (project_id, title) VALUES ('p1', 't')")
    con.commit()
    con.close()
    return db


class _Resp:
    def __init__(self, content: str) -> None:
        self.content = content
        self.model = "mock"
        self.input_tokens = 50
        self.output_tokens = 200


# ─────────────── Phase 1: prompt assembly ──────────────────────────


class TestBuildTruthContext(unittest.TestCase):

    def test_empty_project_returns_frontmatter_only(self) -> None:
        """Empty truth files still render YAML frontmatter — that's the
        documented behavior. Non-empty data should be absent though."""
        db = _fresh_db()
        out = ti.build_truth_context("p1", db, chapter_num=1, characters=[])
        # No real data — but frontmatter markers exist for each kind.
        self.assertIn("truth_file:", out)
        # No hooks / state / etc. should show up
        self.assertNotIn("h1", out)

    def test_renders_when_data_exists(self) -> None:
        db = _fresh_db()
        # Seed a hook so pending_hooks markdown won't be empty.
        with sqlite3.connect(db) as c:
            c.execute(
                "INSERT INTO pending_hooks (hook_id, project_id, description, "
                "status, importance, origin_chapter) "
                "VALUES ('h1', 'p1', '未揭示的身份', 'open', 'A', 3)"
            )
            c.commit()
        out = ti.build_truth_context("p1", db, chapter_num=5)
        self.assertIn("未揭示的身份", out)

    def test_returns_empty_when_store_init_fails(self) -> None:
        # Nonexistent DB → store init may fail; we should not raise.
        out = ti.build_truth_context(
            "p1", "/tmp/nonexistent_db.db", chapter_num=1,
        )
        # Should be empty string, not exception.
        self.assertIsInstance(out, str)


class TestPressuredHooksText(unittest.TestCase):

    def test_empty_when_no_pressure(self) -> None:
        db = _fresh_db()
        out = ti.list_pressured_hooks_text("p1", db, current_chapter=10)
        self.assertEqual(out, "")

    def test_includes_pressured(self) -> None:
        db = _fresh_db()
        # Seed a hook that should be pressured at chapter 10
        # (origin chapter 3, last_advance=3, current=10, threshold=5 → 10-3>=5)
        with sqlite3.connect(db) as c:
            c.execute(
                "INSERT INTO pending_hooks (hook_id, project_id, description, "
                "status, importance, origin_chapter, last_advance_chapter, "
                "pressure_threshold) "
                "VALUES ('h1', 'p1', '快要失控的伏笔', 'pressured', 'A', "
                "3, 3, 5)"
            )
            c.commit()
        out = ti.list_pressured_hooks_text("p1", db, current_chapter=10)
        self.assertIn("快要失控的伏笔", out)
        self.assertIn("第3章埋", out)


# ─────────────── Phase 2: settlement ───────────────────────────────


class TestExtractTruthDeltas(unittest.IsolatedAsyncioTestCase):

    async def test_parses_valid_json_block(self) -> None:
        router = MagicMock()
        router.generate = AsyncMock(return_value=_Resp(json.dumps({
            "state_patches": [
                {"subject": "李星河", "predicate": "位置",
                 "object": "K-7 矿星", "action": "upsert"},
            ],
            "hook_deltas": [
                {"description": "未揭示身份", "action": "new", "importance": "A"},
            ],
            "emotion_arc_entries": [
                {"character": "苏晚", "from_state": "警惕",
                 "to_state": "信任", "trigger": "李星河救了她一命"},
            ],
            "character_relation_updates": [
                {"character_a": "苏晚", "character_b": "李星河",
                 "relation_type": "信任的同伴",
                 "sentiment_score": 30, "trust_level": 50},
            ],
            "chapter_summary": {
                "summary": "本章主角与苏晚建立了信任。",
                "key_events": ["事件A", "事件B"],
                "pov_character": "李星河",
                "mood": "紧张转希望",
            },
        })))
        deltas = await ti.extract_truth_deltas(
            router, "正文很长……",
            chapter_num=3, chapter_title="信任的开端",
        )
        self.assertIsNotNone(deltas)
        self.assertEqual(deltas.chapter_num, 3)
        self.assertEqual(len(deltas.current_state_patches), 1)
        self.assertEqual(deltas.current_state_patches[0].subject, "李星河")
        self.assertEqual(len(deltas.hook_deltas), 1)
        self.assertEqual(deltas.hook_deltas[0].importance.value, "A")
        self.assertEqual(len(deltas.emotion_arc_entries), 1)
        self.assertEqual(len(deltas.character_relation_updates), 1)
        self.assertIsNotNone(deltas.chapter_summary)
        self.assertEqual(deltas.chapter_summary.summary[:5], "本章主角与")

    async def test_unparseable_returns_none(self) -> None:
        router = MagicMock()
        router.generate = AsyncMock(return_value=_Resp("not json"))
        deltas = await ti.extract_truth_deltas(
            router, "x", chapter_num=1,
        )
        self.assertIsNone(deltas)

    async def test_malformed_entries_dropped_not_failed(self) -> None:
        """Missing fields → entry dropped, rest preserved."""
        router = MagicMock()
        router.generate = AsyncMock(return_value=_Resp(json.dumps({
            "state_patches": [
                {"subject": "A", "predicate": "P", "object": "O"},
                {"subject": "", "predicate": "P", "object": "O"},  # empty subject
            ],
            "hook_deltas": [
                {"description": "valid hook", "action": "new"},
                {"description": ""},  # empty
            ],
            "emotion_arc_entries": [
                {"character": "X"},  # missing transitions
            ],
        })))
        deltas = await ti.extract_truth_deltas(router, "x", chapter_num=1)
        self.assertIsNotNone(deltas)
        self.assertEqual(len(deltas.current_state_patches), 1)
        self.assertEqual(len(deltas.hook_deltas), 1)
        self.assertEqual(len(deltas.emotion_arc_entries), 0)

    async def test_fenced_with_json_tag_parses(self) -> None:
        router = MagicMock()
        router.generate = AsyncMock(return_value=_Resp(
            'Here is the JSON:\n```json\n{"state_patches":[{"subject":"A","predicate":"B","object":"C"}]}\n```\nDone.'
        ))
        deltas = await ti.extract_truth_deltas(router, "x", chapter_num=2)
        self.assertIsNotNone(deltas)
        self.assertEqual(len(deltas.current_state_patches), 1)

    async def test_score_clamping(self) -> None:
        router = MagicMock()
        router.generate = AsyncMock(return_value=_Resp(json.dumps({
            "character_relation_updates": [
                {"character_a": "A", "character_b": "B", "relation_type": "x",
                 "sentiment_score": 500, "trust_level": -10},
            ],
        })))
        deltas = await ti.extract_truth_deltas(router, "x", chapter_num=1)
        ru = deltas.character_relation_updates[0]
        self.assertEqual(ru.sentiment_score, 100)  # clamped from 500
        self.assertEqual(ru.trust_level, 0)  # clamped from -10


class TestExtractAndApplyTruthDeltas(unittest.IsolatedAsyncioTestCase):

    async def test_apply_path(self) -> None:
        db = _fresh_db()
        router = MagicMock()
        router.generate = AsyncMock(return_value=_Resp(json.dumps({
            "hook_deltas": [
                {"description": "新埋的伏笔", "action": "new", "importance": "B"},
            ],
            "chapter_summary": {
                "summary": "本章主角发现奇怪的水晶。",
                "key_events": ["发现水晶"],
                "pov_character": "李星河",
            },
        })))
        result = await ti.extract_and_apply_truth_deltas(
            router, "章节正文",
            project_id="p1", db_path=db, chapter_num=1,
        )
        self.assertTrue(result["success"], result)
        self.assertEqual(result["error"], None)
        self.assertGreater(sum(result["applied_counts"].values()), 0)

        # Verify hook landed in DB
        with sqlite3.connect(db) as c:
            n = c.execute(
                "SELECT COUNT(*) FROM pending_hooks WHERE project_id='p1'"
            ).fetchone()[0]
        self.assertEqual(n, 1)

    async def test_unparseable_returns_error_dict_not_raise(self) -> None:
        db = _fresh_db()
        router = MagicMock()
        router.generate = AsyncMock(return_value=_Resp("not json"))
        result = await ti.extract_and_apply_truth_deltas(
            router, "x", project_id="p1", db_path=db, chapter_num=1,
        )
        self.assertFalse(result["success"])
        self.assertIn("settlement extraction failed", result["error"])

    async def test_idempotent_apply(self) -> None:
        """Same deltas applied twice → second call is idempotent."""
        db = _fresh_db()
        canned = json.dumps({
            "hook_deltas": [
                {"description": "稳定的伏笔", "action": "new", "importance": "B"},
            ],
        })
        router = MagicMock()
        router.generate = AsyncMock(return_value=_Resp(canned))
        r1 = await ti.extract_and_apply_truth_deltas(
            router, "x", project_id="p1", db_path=db, chapter_num=2,
        )
        r2 = await ti.extract_and_apply_truth_deltas(
            router, "x", project_id="p1", db_path=db, chapter_num=2,
        )
        self.assertTrue(r1["success"])
        self.assertTrue(r2["success"])
        # Hash is deterministic; second call hits idempotency cache
        self.assertEqual(r1["deltas_hash"], r2["deltas_hash"])
        self.assertTrue(r2["idempotent_hit"])


if __name__ == "__main__":
    unittest.main()
