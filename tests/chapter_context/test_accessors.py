"""Unit tests for ChapterContext agent-field accessors.

These accessors are the single-source-of-truth mapping from
``loader_blocks`` (15-loader output) to the per-field context an agent
actually consumes. They MUST be tolerant to missing / None / non-string
block values, because real projects routinely have legacy data and
loaders that opt out (returning None)."""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from ui.backend.app.services.chapter_context import ChapterContext


def _ctx(**kwargs) -> ChapterContext:
    """Build a ChapterContext with sensible identifier defaults."""
    kwargs.setdefault("project_id", "p1")
    kwargs.setdefault("chapter_id", "ch1")
    return ChapterContext(**kwargs)


# ─────────── _block_content ───────────


class TestBlockContent(unittest.TestCase):

    def test_returns_string_value_stripped(self) -> None:
        ctx = _ctx(loader_blocks={"worldbook": "  body  "})
        self.assertEqual(ctx._block_content("worldbook"), "body")

    def test_missing_block_returns_empty(self) -> None:
        ctx = _ctx(loader_blocks={})
        self.assertEqual(ctx._block_content("worldbook"), "")

    def test_none_value_returns_empty(self) -> None:
        ctx = _ctx(loader_blocks={"worldbook": None})  # type: ignore[dict-item]
        self.assertEqual(ctx._block_content("worldbook"), "")

    def test_render_exception_returns_empty(self) -> None:
        """A non-string value whose .render() raises must not propagate."""
        class Boom:
            target = 1
            def render(self, _budget): raise RuntimeError("boom")

        ctx = _ctx(loader_blocks={"worldbook": Boom()})  # type: ignore[dict-item]
        self.assertEqual(ctx._block_content("worldbook"), "")


# ─────────── world_rules_text ───────────


class TestWorldRulesText(unittest.TestCase):

    def test_concatenates_4_loaders(self) -> None:
        ctx = _ctx(loader_blocks={
            "worldbook":   "WB-body",
            "reference":   "REF-body",
            "skills":      "SK-body",
            "inspiration": "INSP-body",
        })
        text = ctx.world_rules_text()
        for needle in ("WB-body", "REF-body", "SK-body", "INSP-body"):
            self.assertIn(needle, text)
        self.assertEqual(text.count("\n\n"), 3)  # 4 parts, 3 separators

    def test_skips_missing(self) -> None:
        ctx = _ctx(loader_blocks={"worldbook": "only-wb"})
        text = ctx.world_rules_text()
        self.assertEqual(text, "only-wb")
        self.assertNotIn("\n\n", text)

    def test_excludes_character_cards_and_truth(self) -> None:
        """Per spec §3.1: character_cards/truth go to their own fields,
        NOT into world_rules anymore."""
        ctx = _ctx(
            loader_blocks={
                "worldbook":       "WB-body",
                "character_cards": "CC-body",
            },
            truth_bundle={"bundle_text": "TB-body", "pressured_hooks_text": ""},
        )
        text = ctx.world_rules_text()
        self.assertIn("WB-body", text)
        self.assertNotIn("CC-body", text)
        self.assertNotIn("TB-body", text)


# ─────────── memory_context_text ───────────


class TestMemoryContextText(unittest.TestCase):

    def test_fresh_mode_excludes_draft(self) -> None:
        ctx = _ctx(
            generation_mode="fresh",
            loader_blocks={
                "reader_memory":         "RM-body",
                "current_chapter_draft": "DRAFT-body",
            },
        )
        text = ctx.memory_context_text()
        self.assertIn("RM-body", text)
        self.assertNotIn("DRAFT-body", text)

    def test_continue_mode_includes_draft(self) -> None:
        ctx = _ctx(
            generation_mode="continue",
            loader_blocks={
                "reader_memory":         "RM-body",
                "current_chapter_draft": "DRAFT-body",
            },
        )
        text = ctx.memory_context_text()
        self.assertIn("RM-body", text)
        self.assertIn("DRAFT-body", text)

    def test_rewrite_from_mode_includes_draft(self) -> None:
        ctx = _ctx(
            generation_mode="rewrite_from",
            loader_blocks={"current_chapter_draft": "DRAFT-body"},
        )
        self.assertIn("DRAFT-body", ctx.memory_context_text())


# ─────────── style_profile_text ───────────


class TestStyleProfileText(unittest.TestCase):

    def test_platform_directive_first(self) -> None:
        ctx = _ctx(loader_blocks={
            "platform_directive":        "PLATFORM-body",
            "user_special_requirements": "SPECIAL-body",
        })
        text = ctx.style_profile_text()
        self.assertLess(
            text.index("PLATFORM-body"),
            text.index("SPECIAL-body"),
        )

    def test_only_platform(self) -> None:
        ctx = _ctx(loader_blocks={"platform_directive": "PLATFORM-body"})
        self.assertEqual(ctx.style_profile_text(), "PLATFORM-body")


# ─────────── narrative_instructions_text ───────────


class TestNarrativeInstructionsText(unittest.TestCase):

    def test_combines_foreshadowing_and_subplots(self) -> None:
        ctx = _ctx(loader_blocks={
            "foreshadowing": "FORE-body",
            "subplots":      "SUB-body",
        })
        text = ctx.narrative_instructions_text()
        self.assertIn("FORE-body", text)
        self.assertIn("SUB-body", text)


# ─────────── truth_bundle_text ───────────


class TestTruthBundleText(unittest.TestCase):

    def test_returns_tfi_bundle(self) -> None:
        ctx = _ctx(truth_bundle={
            "bundle_text":          "TFI-body",
            "pressured_hooks_text": "",
        })
        self.assertEqual(ctx.truth_bundle_text(), "TFI-body")

    def test_dedup_when_loader_overlaps(self) -> None:
        """If the storyland_state loader content is byte-identical to
        the TFI bundle_text, we don't repeat it."""
        ctx = _ctx(
            loader_blocks={"storyland_state": "TFI-body"},
            truth_bundle={"bundle_text": "TFI-body"},
        )
        text = ctx.truth_bundle_text()
        self.assertEqual(text.count("TFI-body"), 1)

    def test_loader_supplements_when_distinct(self) -> None:
        ctx = _ctx(
            loader_blocks={"storyland_state": "SL-extra"},
            truth_bundle={"bundle_text": "TFI-body"},
        )
        text = ctx.truth_bundle_text()
        self.assertIn("TFI-body", text)
        self.assertIn("SL-extra", text)

    def test_empty_truth_bundle(self) -> None:
        ctx = _ctx(truth_bundle={})
        self.assertEqual(ctx.truth_bundle_text(), "")


# ─────────── to_scene_director_kwargs ───────────


class TestToSceneDirectorKwargs(unittest.TestCase):

    def test_complete_15_loaders(self) -> None:
        """Inject ALL 14 loader blocks + user_special_requirements +
        truth_bundle. Verify each one lands in the right kwargs slot —
        this is the core acceptance test of the migration."""
        ctx = _ctx(
            chapter_num=7,
            user_special_requirements="SPECIAL-REQ",
            loader_blocks={
                "platform_directive":        "PD-body",
                "user_preferences":          "UP-body",
                "user_special_requirements": "USR-loader-body",
                "chapter_outline":           "CO-body",
                "character_cards":           "CC-body",
                "worldbook":                 "WB-body",
                "reference":                 "REF-body",
                "foreshadowing":             "FORE-body",
                "inspiration":               "INSP-body",
                "current_chapter_draft":     "DRAFT-body",
                "subplots":                  "SUB-body",
                "skills":                    "SK-body",
                "storyland_state":           "SL-body",
                "reader_memory":             "RM-body",
            },
            truth_bundle={
                "bundle_text":          "TB-body",
                "pressured_hooks_text": "HOOK-body",
            },
        )
        kw = ctx.to_scene_director_kwargs()
        self.assertEqual(kw["chapter_num"], 7)
        # user_special_requirements > chapter_outline (it's the user-provided override)
        self.assertEqual(kw["chapter_outline"], "SPECIAL-REQ")
        # world_rules carries the 4 worldbuilding loaders
        for s in ("WB-body", "REF-body", "SK-body", "INSP-body"):
            self.assertIn(s, kw["world_rules"])
        # character_cards goes to its own field
        self.assertEqual(kw["character_cards"], "CC-body")
        # memory_context carries reader_memory (fresh mode → no draft)
        self.assertIn("RM-body", kw["memory_context"])
        # narrative goes into constraints (SceneDirector has no narrative field)
        self.assertIn("FORE-body", kw["constraints"])
        self.assertIn("SUB-body", kw["constraints"])

    def test_falls_back_to_chapter_outline_loader(self) -> None:
        ctx = _ctx(loader_blocks={"chapter_outline": "CO-body"})
        kw = ctx.to_scene_director_kwargs()
        self.assertEqual(kw["chapter_outline"], "CO-body")

    def test_all_empty_blocks_dont_crash(self) -> None:
        ctx = _ctx()
        kw = ctx.to_scene_director_kwargs()
        for key in ("chapter_outline", "memory_context", "world_rules",
                    "character_cards", "constraints"):
            self.assertEqual(kw[key], "")

    def test_9_previously_missing_loaders_reach_kwargs(self) -> None:
        """Migration acceptance: verify the 9 loaders that the OLD
        6-block fold dropped now reach agent kwargs."""
        ctx = _ctx(
            user_special_requirements="USR-body",
            loader_blocks={
                "platform_directive":        "PD-body",
                "user_preferences":          "UP-body",
                "subplots":                  "SUB-body",
                "foreshadowing":             "FORE-body",
                "inspiration":               "INSP-body",
                # plus a worldbook so world_rules is non-empty
                "worldbook":                 "WB-body",
            },
        )
        kw = ctx.to_scene_director_kwargs()
        # 4 of the previously-missing loaders should now be reachable
        self.assertIn("USR-body", kw["chapter_outline"])
        self.assertIn("INSP-body", kw["world_rules"])
        self.assertIn("SUB-body", kw["constraints"])
        self.assertIn("FORE-body", kw["constraints"])
        # platform_directive + user_preferences are consumed by Writer kwargs,
        # not SceneDirector — see TestToWriterAssembleKwargs


# ─────────── to_writer_assemble_kwargs ───────────


class TestToWriterAssembleKwargs(unittest.TestCase):

    def test_routes_loaders_to_writer_fields(self) -> None:
        ctx = _ctx(
            chapter_num=3,
            loader_blocks={
                "platform_directive":        "PD-body",
                "user_special_requirements": "USR-body",
                "user_preferences":          "UP-body",
                "reader_memory":             "RM-body",
                "foreshadowing":             "FORE-body",
                "subplots":                  "SUB-body",
            },
            truth_bundle={
                "bundle_text":          "TB-body",
                "pressured_hooks_text": "HOOK-body",
            },
        )
        kw = ctx.to_writer_assemble_kwargs()
        self.assertEqual(kw["chapter_num"], 3)
        # style_profile = platform_directive + user_special_requirements
        self.assertIn("PD-body", kw["style_profile"])
        self.assertIn("USR-body", kw["style_profile"])
        # user_preferences in its own field
        self.assertEqual(kw["user_preferences"], "UP-body")
        # narrative + pressured_hooks
        self.assertIn("FORE-body", kw["narrative_instructions"])
        self.assertIn("SUB-body", kw["narrative_instructions"])
        self.assertIn("HOOK-body", kw["narrative_instructions"])
        self.assertIn("待推进伏笔", kw["narrative_instructions"])
        # memory + truth bundle
        self.assertIn("RM-body", kw["memory_context"])
        self.assertIn("TB-body", kw["truth_bundle"])

    def test_no_performance_records_in_kwargs(self) -> None:
        """performance_records / narrator_text are runtime-only; the
        accessor must NOT pre-fill them (caller will)."""
        ctx = _ctx()
        kw = ctx.to_writer_assemble_kwargs()
        self.assertNotIn("performance_records", kw)
        self.assertNotIn("narrator_text", kw)

    def test_omits_pressured_hooks_header_when_empty(self) -> None:
        ctx = _ctx(
            loader_blocks={"foreshadowing": "FORE-body"},
            truth_bundle={"pressured_hooks_text": ""},
        )
        kw = ctx.to_writer_assemble_kwargs()
        self.assertIn("FORE-body", kw["narrative_instructions"])
        self.assertNotIn("待推进伏笔", kw["narrative_instructions"])

    def test_all_empty_blocks_dont_crash(self) -> None:
        ctx = _ctx()
        kw = ctx.to_writer_assemble_kwargs()
        for key in ("narrative_instructions", "style_profile",
                    "user_preferences", "memory_context", "truth_bundle"):
            self.assertEqual(kw[key], "")


if __name__ == "__main__":
    unittest.main()
