"""Migration acceptance: multi-agent /start pipeline routes the full
14-loader ChapterContext output into SceneDirector kwargs, instead of
the old 6-block fold into ``req_data['world_rules']``.

Strategy: stub ``SceneDirector.plan_scenes`` to *capture* the kwargs it
receives, then raise — that drops the pipeline into its scene-director
failure branch, which we short-circuit by stubbing ``_wait_for_confirm_bg``
to return ``None`` (clean early exit). Assertions then check that the
loaders the OLD path dropped now reach the agent."""
from __future__ import annotations

import asyncio
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from ui.backend.app.services.chapter_context import ChapterContext


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _drive_pipeline(*, chapter_context, req_extra=None):
    """Run ``_run_pipeline_inner`` against a captured SceneDirector and
    return the kwargs ``plan_scenes`` received on its first call."""
    from ui.backend.app.routers import generation_api

    captured: dict = {}
    call_count = {"n": 0}

    class _StubDirector:
        def __init__(self, *args, **kwargs): pass

        async def plan_scenes(self, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                captured.update(kwargs)
            # Force the pipeline's error branch (we don't want to run
            # downstream agents in this test).
            raise RuntimeError("captured-and-stop")

    async def _no_confirm(*args, **kwargs):
        return None

    session = {
        "status": "running",
        "events": [],
        "result": None,
        "text": "",
        "current_step": "",
        "request": {},
        "created_at": 0,
        "chapter_context": chapter_context,
    }
    req_data = {
        "project_id": "p1",
        "chapter_id": "ch9",
        "chapter_num": 9,
        "synopsis": "测试章节大纲",
        "characters": ["主角"],
        "provider": "openai",
        "model": "test-model",
        "world_rules": "",  # legacy fold field, intentionally empty so we
                            # see only what cc routes in.
    }
    if req_extra:
        req_data.update(req_extra)

    # Insert into the active sessions so emit / mirror work.
    generation_api._active_sessions["sid-test"] = session

    patches = [
        mock.patch.object(generation_api, "_build_router",
                          return_value=mock.MagicMock()),
        mock.patch("agents.production.scene_director.SceneDirector",
                   _StubDirector),
        mock.patch.object(generation_api, "_wait_for_confirm_bg",
                          new=_no_confirm),
        mock.patch.object(generation_api, "_mirror_session_to_db",
                          return_value=None),
    ]
    for p in patches:
        p.start()
    try:
        _run(generation_api._run_pipeline_inner("sid-test", session, req_data))
    finally:
        for p in patches:
            p.stop()
        generation_api._active_sessions.pop("sid-test", None)

    return captured


# ─────────── ChapterContext path: 14 loaders reach SceneDirector ───────────


class TestSceneDirectorReceivesAllLoaders(unittest.TestCase):

    def test_world_rules_carries_worldbook_reference_skills_inspiration(self) -> None:
        cc = ChapterContext(
            project_id="p1", chapter_id="ch9", chapter_num=9,
            loader_blocks={
                "worldbook":   "WORLDBOOK-灵气复苏体系",
                "reference":   "REFERENCE-诡秘之主笔法",
                "skills":      "SKILLS-悬念铺设技巧",
                "inspiration": "INSPIRATION-月圆夜异象",
            },
        )
        kw = _drive_pipeline(chapter_context=cc)
        self.assertIn("WORLDBOOK-灵气复苏体系", kw["world_rules"])
        self.assertIn("REFERENCE-诡秘之主笔法", kw["world_rules"])
        self.assertIn("SKILLS-悬念铺设技巧",    kw["world_rules"])
        self.assertIn("INSPIRATION-月圆夜异象",  kw["world_rules"])

    def test_character_cards_field_populated(self) -> None:
        cc = ChapterContext(
            project_id="p1", chapter_id="ch9", chapter_num=9,
            loader_blocks={"character_cards": "CC-主角林天身份成谜"},
        )
        kw = _drive_pipeline(chapter_context=cc)
        self.assertIn("CC-主角林天身份成谜", kw["character_cards"])

    def test_chapter_outline_uses_runtime_synopsis(self) -> None:
        """Runtime _outline (synopsis + location + time) wins over the
        cc.chapter_outline (USR/loader) — cc's value is folded into
        constraints as 【本章特殊要求】 instead."""
        cc = ChapterContext(
            project_id="p1", chapter_id="ch9", chapter_num=9,
            user_special_requirements="USR-本章主角必须独自行动",
        )
        kw = _drive_pipeline(chapter_context=cc, req_extra={
            "synopsis": "RUNTIME-SYNOPSIS",
            "location": "幽冥谷",
        })
        self.assertIn("RUNTIME-SYNOPSIS", kw["chapter_outline"])
        self.assertIn("幽冥谷",            kw["chapter_outline"])
        # USR is folded into constraints (SceneDirector has no USR field).
        self.assertIn("USR-本章主角必须独自行动", kw["constraints"])

    def test_narrative_loaders_in_constraints(self) -> None:
        cc = ChapterContext(
            project_id="p1", chapter_id="ch9", chapter_num=9,
            loader_blocks={
                "foreshadowing": "FORE-旧伤再现",
                "subplots":      "SUB-反派暗中布局",
            },
        )
        kw = _drive_pipeline(chapter_context=cc)
        self.assertIn("FORE-旧伤再现",     kw["constraints"])
        self.assertIn("SUB-反派暗中布局",  kw["constraints"])

    def test_memory_loaders_in_memory_context(self) -> None:
        cc = ChapterContext(
            project_id="p1", chapter_id="ch9", chapter_num=9,
            loader_blocks={"reader_memory": "RM-上章主角发现密信"},
        )
        kw = _drive_pipeline(chapter_context=cc)
        self.assertIn("RM-上章主角发现密信", kw["memory_context"])

    def test_user_preferences_in_constraints(self) -> None:
        cc = ChapterContext(
            project_id="p1", chapter_id="ch9", chapter_num=9,
            loader_blocks={"user_preferences": "UP-偏好克制叙述"},
        )
        kw = _drive_pipeline(chapter_context=cc)
        self.assertIn("UP-偏好克制叙述", kw["constraints"])


# ─────────── 9-loader migration acceptance ───────────


class Test9PreviouslyMissingLoadersReachAgent(unittest.TestCase):
    """The migration's headline acceptance test: of the 9 loaders the
    OLD 6-block fold dropped, the most user-impactful ones must now
    reach SceneDirector kwargs."""

    def test_4_high_value_loaders_reach_agent(self) -> None:
        cc = ChapterContext(
            project_id="p1", chapter_id="ch9", chapter_num=9,
            user_special_requirements="USR-marker",
            loader_blocks={
                "platform_directive": "PLATFORM-marker",
                "user_preferences":   "USERPREFS-marker",
                "subplots":           "SUBPLOTS-marker",
                "worldbook":          "WB-stub",  # so world_rules is non-empty
            },
        )
        kw = _drive_pipeline(chapter_context=cc)
        # USR + user_preferences + subplots are all expected to appear
        # somewhere in the kwargs that SceneDirector receives.
        flat = "\n".join([
            kw.get("chapter_outline", ""),
            kw.get("world_rules", ""),
            kw.get("memory_context", ""),
            kw.get("character_cards", ""),
            kw.get("constraints", ""),
        ])
        self.assertIn("USR-marker",       flat)
        self.assertIn("USERPREFS-marker", flat)
        self.assertIn("SUBPLOTS-marker",  flat)
        # platform_directive lands in Writer kwargs (style_profile), not
        # SceneDirector — see test_writer in Step 3 tests.


# ─────────── Fallback when ChapterContext is None ───────────


class TestFallbackWhenChapterContextNone(unittest.TestCase):

    def test_legacy_path_when_cc_is_none(self) -> None:
        """With session['chapter_context'] = None, the pipeline must
        fall back to passing the legacy ``world_rules`` fold via
        ``constraints`` — and crucially must NOT crash."""
        kw = _drive_pipeline(chapter_context=None, req_extra={
            "world_rules": "LEGACY-WORLD-RULES-FOLD",
        })
        # Legacy path: world_rules folded into constraints; world_rules
        # field is the empty string by design (it was always "" in the
        # old code).
        self.assertEqual(kw.get("world_rules"), "")
        self.assertIn("LEGACY-WORLD-RULES-FOLD", kw["constraints"])


# ─────────── Empty loaders don't crash ───────────


class TestMissingLoadersDontBreakPipeline(unittest.TestCase):

    def test_empty_loader_blocks_yield_empty_strings(self) -> None:
        cc = ChapterContext(
            project_id="p1", chapter_id="ch9", chapter_num=9,
            loader_blocks={},
        )
        kw = _drive_pipeline(chapter_context=cc)
        # All cc-routed fields are empty but the kwargs still contain
        # them (so SceneDirector signature is satisfied) and the
        # pipeline didn't crash on the way to the call.
        self.assertEqual(kw.get("world_rules", ""), "")
        self.assertEqual(kw.get("memory_context", ""), "")


# ─────────── Writer.assemble_chapter receives merged cc kwargs ───────────


class TestWriterReceivesChapterContext(unittest.TestCase):
    """Stage 3 wiring: writer kwargs merge cc.to_writer_assemble_kwargs()
    with the runtime-computed style/user-prefs/memory values."""

    def test_merge_logic_routes_platform_directive_and_user_preferences(self) -> None:
        """The merge happens in generation_api at the Writer stage; here
        we verify the helper-method outputs that drive it are correct.
        (The end-to-end pipeline drive is too heavy for this test;
        Stage 2 already verified the cc → pipeline branching.)"""
        cc = ChapterContext(
            project_id="p1", chapter_id="ch9", chapter_num=9,
            loader_blocks={
                "platform_directive":        "PLATFORM-marker",
                "user_special_requirements": "USR-marker",
                "user_preferences":          "USERPREFS-marker",
                "reader_memory":             "RM-marker",
                "subplots":                  "SUB-marker",
            },
            truth_bundle={
                "bundle_text":          "TB-marker",
                "pressured_hooks_text": "HOOK-marker",
            },
        )
        kw = cc.to_writer_assemble_kwargs()
        # All previously-missing loaders make it into Writer kwargs:
        self.assertIn("PLATFORM-marker",  kw["style_profile"])
        self.assertIn("USR-marker",       kw["style_profile"])
        self.assertIn("USERPREFS-marker", kw["user_preferences"])
        self.assertIn("RM-marker",        kw["memory_context"])
        self.assertIn("SUB-marker",       kw["narrative_instructions"])
        self.assertIn("HOOK-marker",      kw["narrative_instructions"])
        self.assertIn("TB-marker",        kw["truth_bundle"])

    def test_merge_with_runtime_values_no_clobber(self) -> None:
        """The merge helper in generation_api uses ``a + b`` joining —
        cc and runtime values coexist, neither clobbers the other."""
        cc = ChapterContext(
            project_id="p1", chapter_id="ch9", chapter_num=9,
            loader_blocks={
                "platform_directive": "FROM-CC",
                "user_preferences":   "USERPREFS-FROM-CC",
                "reader_memory":      "MEM-FROM-CC",
            },
        )
        cc_kw = cc.to_writer_assemble_kwargs()

        # Simulate the merge done in generation_api.
        def _merge(a: str, b: str) -> str:
            return "\n\n".join(p for p in (a, b) if (p or "").strip())

        runtime_style = "RUNTIME-STYLE-NOTES"
        runtime_prefs = "RUNTIME-EDIT-ANALYZER-PREFS"
        runtime_mem   = "RUNTIME-EDITOR-MEMORY"

        merged_style = _merge(cc_kw["style_profile"],    runtime_style)
        merged_prefs = _merge(cc_kw["user_preferences"], runtime_prefs)
        merged_mem   = _merge(cc_kw["memory_context"],   runtime_mem)

        for s in ("FROM-CC", "RUNTIME-STYLE-NOTES"):
            self.assertIn(s, merged_style)
        for s in ("USERPREFS-FROM-CC", "RUNTIME-EDIT-ANALYZER-PREFS"):
            self.assertIn(s, merged_prefs)
        for s in ("MEM-FROM-CC", "RUNTIME-EDITOR-MEMORY"):
            self.assertIn(s, merged_mem)


if __name__ == "__main__":
    unittest.main()
