"""Stage 4: pydantic Request/Response schemas for the 4 production agents.

Each agent now has a ``*_typed`` method that takes a typed Request and
returns a typed Response. The legacy method is unchanged, so existing
callers in ``generation_api.py`` keep working — new callers can adopt
the typed API one at a time.

These tests lock down:
  - Request → ``to_legacy_kwargs()`` round-trip
  - Response ``from_legacy_dict()`` / ``from_legacy_list()`` tolerance
    of empty / partial / extra-key dicts the LLM emits
  - Typed wrappers on each agent forward to the legacy method correctly
"""
from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))


from agents.contracts import (
    CharacterInstruction,
    DimensionScores,
    EvaluationIssue,
    EvaluatorRequest,
    EvaluatorResponse,
    KnowledgeBoundary,
    SceneDirectorRequest,
    SceneDirectorResponse,
    ScenePlanItem,
    SceneSimulationResult,
    SceneSimulatorRequest,
    SceneSimulatorResponse,
    WriterAssembleRequest,
    WriterResponse,
    WriterWriteRequest,
)


# ─────────── SceneDirector ───────────


class TestSceneDirectorContracts(unittest.TestCase):

    def test_request_to_legacy_kwargs_round_trip(self) -> None:
        req = SceneDirectorRequest(
            chapter_outline="第3章：主角进城", chapter_num=3,
            memory_context="前情：师傅死亡",
            world_rules="灵气复苏",
            character_cards="## 主角\n剑客", constraints="不要 OOC",
        )
        kw = req.to_legacy_kwargs()
        self.assertEqual(kw["chapter_outline"], "第3章：主角进城")
        self.assertEqual(kw["chapter_num"], 3)
        self.assertEqual(kw["world_rules"], "灵气复苏")
        # All 6 plan_scenes kwargs present.
        self.assertEqual(
            set(kw.keys()),
            {"chapter_outline", "chapter_num", "memory_context",
             "world_rules", "character_cards", "constraints"},
        )

    def test_response_from_legacy_dict_full_shape(self) -> None:
        raw = {
            "chapter_num": 3,
            "scenes": [{
                "scene_index": 0,
                "location": "城门口", "time": "黄昏",
                "characters": ["主角", "守卫"],
                "summary": "主角与守卫对峙",
                "beats": ["主角靠近", "守卫拦截"],
                "character_instructions": {
                    "主角": {
                        "emotional_state": "警惕",
                        "secret_goal": "找师傅",
                        "knowledge_boundary": {
                            "restricted_facts": ["守卫是密探"],
                            "must_not_know": ["师傅死亡"],
                        },
                        "must": ["保持冷静"],
                        "must_not": ["亮剑"],
                    },
                },
                "narrator_instructions": "突出紧张感",
            }],
            "chapter_arc": "悬念升级",
        }
        resp = SceneDirectorResponse.from_legacy_dict(raw)
        self.assertEqual(resp.chapter_num, 3)
        self.assertEqual(len(resp.scenes), 1)
        scene = resp.scenes[0]
        self.assertEqual(scene.location, "城门口")
        self.assertEqual(scene.characters, ["主角", "守卫"])
        instr = scene.character_instructions["主角"]
        self.assertIsInstance(instr, CharacterInstruction)
        self.assertEqual(instr.emotional_state, "警惕")
        self.assertIsInstance(instr.knowledge_boundary, KnowledgeBoundary)
        self.assertEqual(instr.knowledge_boundary.restricted_facts,
                          ["守卫是密探"])
        self.assertFalse(resp.is_fallback)

    def test_response_from_legacy_dict_tolerates_empty(self) -> None:
        # None → empty default response (no crash).
        self.assertEqual(SceneDirectorResponse.from_legacy_dict(None).scenes, [])
        # {} → also empty.
        self.assertEqual(SceneDirectorResponse.from_legacy_dict({}).scenes, [])

    def test_is_fallback_detects_underscore_diagnostics(self) -> None:
        resp = SceneDirectorResponse.from_legacy_dict({
            "scenes": [], "chapter_arc": "",
            "_fallback": True, "_error": "json parse",
        })
        self.assertTrue(resp.is_fallback)
        # Extras preserved by extra="allow"
        self.assertEqual(resp.model_extra.get("_error"), "json parse")

    def test_extra_keys_preserved_for_forward_compat(self) -> None:
        """LLM emits a future field 'mood_track' that the schema doesn't
        know about — extra=allow lets us keep it."""
        resp = SceneDirectorResponse.from_legacy_dict({
            "scenes": [{
                "scene_index": 0, "summary": "x",
                "characters": ["A"],
                "mood_track": "rising",  # extra
            }],
            "chapter_arc": "",
        })
        scene = resp.scenes[0]
        self.assertEqual(scene.model_extra.get("mood_track"), "rising")


# ─────────── SceneSimulator ───────────


class TestSceneSimulatorContracts(unittest.TestCase):

    def test_request_accepts_scene_plan_items_and_dicts(self) -> None:
        items = [
            ScenePlanItem(scene_index=0, summary="A", characters=["x"]),
            {"scene_index": 1, "summary": "B", "characters": ["y"]},
        ]
        req = SceneSimulatorRequest(scene_plans=items, character_cards={
            "x": "X card",
        })
        kw = req.to_legacy_kwargs()
        # Both downgrade to plain dicts for the legacy callee.
        self.assertEqual(len(kw["scene_plans"]), 2)
        self.assertEqual(kw["scene_plans"][0]["scene_index"], 0)
        self.assertEqual(kw["scene_plans"][1]["scene_index"], 1)
        self.assertEqual(kw["character_cards"]["x"], "X card")

    def test_response_from_legacy_list(self) -> None:
        raw = [
            {"performances": {"主角": "出招"}, "narrator": "天亮了",
             "combined": "[旁白]\n天亮了\n[主角]\n出招"},
            {"performances": {"守卫": "拦截"}, "narrator": "风起",
             "combined": "[旁白]\n风起\n[守卫]\n拦截"},
        ]
        resp = SceneSimulatorResponse.from_legacy_list(raw)
        self.assertEqual(len(resp.scene_results), 2)
        self.assertIsInstance(resp.scene_results[0], SceneSimulationResult)
        self.assertEqual(resp.scene_results[0].performances, {"主角": "出招"})
        # Round-trip back to list-of-dicts shape.
        out = resp.to_legacy_list()
        self.assertEqual(out[0]["narrator"], "天亮了")

    def test_combined_performance_records_flattens(self) -> None:
        resp = SceneSimulatorResponse.from_legacy_list([
            {"performances": {"主角": "甲", "守卫": "乙"}, "narrator": "X",
             "combined": ""},
            {"performances": {"主角": "丙"}, "narrator": "Y", "combined": ""},
        ])
        records = resp.combined_performance_records()
        self.assertEqual(len(records), 3)
        # Each record contains actor name + perf text.
        self.assertTrue(any("甲" in r and "主角" in r for r in records))

    def test_combined_narrator_text_joins(self) -> None:
        resp = SceneSimulatorResponse.from_legacy_list([
            {"performances": {}, "narrator": "A", "combined": ""},
            {"performances": {}, "narrator": "", "combined": ""},  # empty skipped
            {"performances": {}, "narrator": "C", "combined": ""},
        ])
        self.assertEqual(resp.combined_narrator_text(), "A\n\nC")


# ─────────── Writer ───────────


class TestWriterContracts(unittest.TestCase):

    def test_assemble_request_round_trip(self) -> None:
        req = WriterAssembleRequest(
            performance_records=["[主角]甲"], narrator_text="N",
            chapter_num=5, chapter_title="第5章",
            style_profile="紧凑", truth_bundle="bundle",
        )
        kw = req.to_legacy_kwargs()
        self.assertEqual(kw["performance_records"], ["[主角]甲"])
        self.assertEqual(kw["chapter_num"], 5)
        self.assertEqual(kw["truth_bundle"], "bundle")

    def test_write_request_round_trip(self) -> None:
        req = WriterWriteRequest(
            outline="主角进城", chapter_num=3,
            characters=["主角"], pov_character="主角",
            target_word_count=3000,
        )
        kw = req.to_legacy_kwargs()
        self.assertEqual(kw["outline"], "主角进城")
        self.assertEqual(kw["characters"], ["主角"])
        self.assertEqual(kw["target_word_count"], 3000)

    def test_response_from_text(self) -> None:
        r = WriterResponse.from_text("第3章 主角进城了。", chapter_num=3)
        self.assertEqual(r.chapter_text, "第3章 主角进城了。")
        self.assertEqual(r.mode, "assembly")
        self.assertEqual(r.char_count, 10)
        r2 = WriterResponse.from_text("X", chapter_num=1, mode="single_writer")
        self.assertEqual(r2.mode, "single_writer")

    def test_response_handles_empty_text(self) -> None:
        r = WriterResponse.from_text("", chapter_num=1)
        self.assertEqual(r.chapter_text, "")
        self.assertEqual(r.char_count, 0)


# ─────────── Evaluator ───────────


class TestEvaluatorContracts(unittest.TestCase):

    def test_request_round_trip(self) -> None:
        req = EvaluatorRequest(
            chapter_text="第3章 ……", chapter_num=3,
            scene_plan={"scenes": [{"summary": "X"}]},
            character_cards="主角", constraints="no OOC",
        )
        kw = req.to_legacy_kwargs()
        self.assertEqual(kw["chapter_text"], "第3章 ……")
        self.assertEqual(kw["scene_plan"]["scenes"][0]["summary"], "X")
        self.assertEqual(kw["max_retries"], 3)

    def test_response_full_shape(self) -> None:
        raw = {
            "passed": False, "score": 55,
            "issues": [
                {"type": "consistency", "dimension": "consistency",
                 "severity": "high", "message": "主角性格突变"},
                {"type": "repetition", "severity": "low",
                 "message": "重复用词"},
            ],
            "strengths": ["节奏好"],
            "dimension_scores": {
                "constraint_satisfaction": 70, "consistency": 60,
                "knowledge_isolation": 90, "repetition": 75,
                "ai_flavor": 80, "narrative_quality": 65,
            },
            "summary_text": "整体不通过",
            "process_log": [{"detector": "约束检查", "status": "通过"}],
        }
        resp = EvaluatorResponse.from_legacy_dict(raw)
        self.assertFalse(resp.passed)
        self.assertEqual(resp.score, 55)
        self.assertEqual(len(resp.issues), 2)
        self.assertIsInstance(resp.issues[0], EvaluationIssue)
        self.assertEqual(resp.issues[0].severity, "high")
        self.assertEqual(resp.high_severity_count, 1)
        self.assertIsInstance(resp.dimension_scores, DimensionScores)
        self.assertEqual(resp.dimension_scores.consistency, 60)

    def test_response_tolerates_empty(self) -> None:
        self.assertEqual(EvaluatorResponse.from_legacy_dict(None).score, 0)
        self.assertEqual(EvaluatorResponse.from_legacy_dict({}).issues, [])
        self.assertTrue(EvaluatorResponse.from_legacy_dict({}).passed)

    def test_response_preserves_raw_evaluation_fallback_key(self) -> None:
        """When JSON parse fails, Evaluator stores raw text in
        'raw_evaluation'. extra='allow' keeps it round-trip safe."""
        resp = EvaluatorResponse.from_legacy_dict({
            "passed": True, "score": 70, "issues": [],
            "raw_evaluation": "模型乱说一通",
        })
        self.assertEqual(resp.model_extra.get("raw_evaluation"), "模型乱说一通")

    def test_response_to_legacy_dict_round_trip(self) -> None:
        raw = {"passed": True, "score": 88, "issues": [],
               "summary_text": "好"}
        resp = EvaluatorResponse.from_legacy_dict(raw)
        out = resp.to_legacy_dict()
        self.assertEqual(out["score"], 88)
        self.assertEqual(out["summary_text"], "好")


# ─────────── Agent class wrappers wired correctly ───────────


class TestAgentTypedWrappers(unittest.IsolatedAsyncioTestCase):

    async def test_scene_director_typed_forwards(self) -> None:
        from agents.production.scene_director import SceneDirector
        sd = SceneDirector.__new__(SceneDirector)
        legacy_return = {"scenes": [{"scene_index": 0, "summary": "ok",
                                       "characters": ["x"]}],
                         "chapter_arc": "rise"}
        with mock.patch.object(
            sd, "plan_scenes", return_value=legacy_return,
        ) as m:
            # Make the mocked legacy plan_scenes awaitable.
            async def _aw(*a, **kw): return legacy_return
            sd.plan_scenes = _aw  # type: ignore[assignment]
            req = SceneDirectorRequest(
                chapter_outline="X", chapter_num=2,
            )
            resp = await sd.plan_scenes_typed(req)
        self.assertIsInstance(resp, SceneDirectorResponse)
        self.assertEqual(len(resp.scenes), 1)
        self.assertEqual(resp.chapter_arc, "rise")

    async def test_simulator_typed_forwards(self) -> None:
        from agents.production.scene_simulator import SceneSimulator
        sim = SceneSimulator.__new__(SceneSimulator)
        legacy_return = [
            {"performances": {"主角": "甲"}, "narrator": "夜", "combined": ""},
        ]
        async def _aw(*a, **kw): return legacy_return
        sim.simulate_chapter = _aw  # type: ignore[assignment]
        req = SceneSimulatorRequest(
            scene_plans=[{"scene_index": 0, "summary": "x", "characters": ["主角"]}],
            character_cards={"主角": "card"},
        )
        resp = await sim.simulate_chapter_typed(req)
        self.assertIsInstance(resp, SceneSimulatorResponse)
        self.assertEqual(len(resp.scene_results), 1)
        self.assertEqual(resp.scene_results[0].narrator, "夜")

    async def test_writer_assemble_typed_forwards(self) -> None:
        from agents.production.writer import Writer
        w = Writer.__new__(Writer)
        async def _aw(*a, **kw): return "## 第3章\n出招"
        w.assemble_chapter = _aw  # type: ignore[assignment]
        req = WriterAssembleRequest(
            performance_records=["[主角]甲"], narrator_text="N",
            chapter_num=3,
        )
        resp = await w.assemble_chapter_typed(req)
        self.assertIsInstance(resp, WriterResponse)
        self.assertEqual(resp.chapter_text, "## 第3章\n出招")
        self.assertEqual(resp.mode, "assembly")
        self.assertEqual(resp.chapter_num, 3)

    async def test_writer_write_typed_forwards(self) -> None:
        from agents.production.writer import Writer
        w = Writer.__new__(Writer)
        async def _aw(*a, **kw): return "single agent prose"
        w.write_chapter = _aw  # type: ignore[assignment]
        req = WriterWriteRequest(outline="X", chapter_num=4)
        resp = await w.write_chapter_typed(req)
        self.assertEqual(resp.chapter_text, "single agent prose")
        self.assertEqual(resp.mode, "single_writer")
        self.assertEqual(resp.chapter_num, 4)

    async def test_evaluator_typed_forwards(self) -> None:
        from agents.evaluation.evaluator import Evaluator
        ev = Evaluator.__new__(Evaluator)
        legacy_return = {
            "passed": True, "score": 82,
            "issues": [{"type": "x", "severity": "low", "message": "y"}],
            "dimension_scores": {"constraint_satisfaction": 85},
            "summary_text": "good",
        }
        async def _aw(*a, **kw): return legacy_return
        ev.evaluate_chapter = _aw  # type: ignore[assignment]
        req = EvaluatorRequest(chapter_text="第3章……", chapter_num=3)
        resp = await ev.evaluate_chapter_typed(req)
        self.assertIsInstance(resp, EvaluatorResponse)
        self.assertEqual(resp.score, 82)
        self.assertTrue(resp.passed)
        self.assertEqual(len(resp.issues), 1)


if __name__ == "__main__":
    unittest.main()
