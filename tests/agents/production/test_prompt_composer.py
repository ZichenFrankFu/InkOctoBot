"""
Tests for agents/production/prompt_composer.py — InkOS B1.
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))

from agents.production.prompt_composer import (
    PromptComposer,
    PromptContext,
    FnBlock,
    HeaderBlock,
    OutlineBlock,
    ChapterMetadataBlock,
    OutputRequirementsBlock,
    StorylandStateBundleContextBlock,
    LedgerAnchorsContextBlock,
    ReaderMemoryContextBlock,
    CharacterCardsContextBlock,
    single_writer_composer,
    assembly_composer,
)


class TestCompositionBasics(unittest.TestCase):

    def test_empty_provider_list_yields_empty(self) -> None:
        c = PromptComposer([])
        self.assertEqual(c.build_user_content(PromptContext()), "")
        self.assertEqual(c.build_context(PromptContext()), "")

    def test_role_routing(self) -> None:
        """user_content blocks → build_user_content; context blocks → build_context."""
        c = PromptComposer([
            FnBlock(name="u", role="user_content", fn=lambda _: "U"),
            FnBlock(name="ctx", role="context", fn=lambda _: "C"),
        ])
        ctx = PromptContext()
        self.assertEqual(c.build_user_content(ctx), "U")
        self.assertEqual(c.build_context(ctx), "C")

    def test_empty_string_blocks_dropped(self) -> None:
        c = PromptComposer([
            FnBlock(name="a", role="user_content", fn=lambda _: "AA"),
            FnBlock(name="b", role="user_content", fn=lambda _: ""),
            FnBlock(name="c", role="user_content", fn=lambda _: "CC"),
        ])
        self.assertEqual(c.build_user_content(PromptContext()), "AA\n\nCC")

    def test_provider_order_preserved(self) -> None:
        c = PromptComposer([
            FnBlock(name="a", role="user_content", fn=lambda _: "1"),
            FnBlock(name="b", role="user_content", fn=lambda _: "2"),
            FnBlock(name="c", role="user_content", fn=lambda _: "3"),
        ])
        self.assertEqual(c.build_user_content(PromptContext()), "1\n\n2\n\n3")


class TestProviders(unittest.TestCase):

    def test_header_block(self) -> None:
        ctx = PromptContext(chapter_num=7, chapter_title="意外发现")
        out = HeaderBlock().render(ctx)
        self.assertIn("第7章", out)
        self.assertIn("意外发现", out)

    def test_header_block_without_title(self) -> None:
        out = HeaderBlock().render(PromptContext(chapter_num=1))
        self.assertIn("第1章", out)
        self.assertNotIn("「", out)  # no opening guillemet when no title

    def test_outline_block_uses_synopsis_fallback(self) -> None:
        out_with_outline = OutlineBlock().render(PromptContext(outline="主大纲"))
        self.assertIn("主大纲", out_with_outline)

        out_with_synopsis = OutlineBlock().render(PromptContext(synopsis="备用梗概"))
        self.assertIn("备用梗概", out_with_synopsis)

        out_with_neither = OutlineBlock().render(PromptContext())
        self.assertEqual(out_with_neither, "")

    def test_chapter_metadata_skip_when_empty(self) -> None:
        out = ChapterMetadataBlock().render(PromptContext())
        self.assertEqual(out, "")

    def test_chapter_metadata_populated(self) -> None:
        ctx = PromptContext(
            time_label="春", location="村口",
            characters=["李星河", "苏晚"], pov_character="李星河",
        )
        out = ChapterMetadataBlock().render(ctx)
        self.assertIn("时间：春", out)
        self.assertIn("地点：村口", out)
        self.assertIn("POV 视角：李星河", out)
        self.assertIn("李星河, 苏晚", out)

    def test_output_requirements_mode_branch(self) -> None:
        single = OutputRequirementsBlock().render(PromptContext(mode="single_writer", target_word_count=1500))
        assembly = OutputRequirementsBlock().render(PromptContext(mode="assembly"))
        self.assertIn("单 Writer 模式", single)
        self.assertIn("1500", single)
        self.assertNotIn("单 Writer 模式", assembly)
        self.assertIn("表演记录", assembly)

    def test_truth_bundle_context_block(self) -> None:
        out = StorylandStateBundleContextBlock().render(PromptContext(truth_bundle="some truth"))
        self.assertIn("[Truth Files 状态权威]", out)
        self.assertIn("some truth", out)

        empty = StorylandStateBundleContextBlock().render(PromptContext())
        self.assertEqual(empty, "")

    def test_character_cards_wrapper(self) -> None:
        out = CharacterCardsContextBlock().render(PromptContext(character_cards="李星河档案"))
        self.assertTrue(out.startswith("[角色档案]"))
        self.assertIn("李星河档案", out)


class TestPrebuiltComposers(unittest.TestCase):

    def test_single_writer_produces_user_and_context(self) -> None:
        ctx = PromptContext(
            mode="single_writer",
            chapter_num=2, chapter_title="初见",
            outline="主角与女主初次相遇",
            characters=["李星河", "苏晚"], pov_character="李星河",
            character_cards="档案 X", world_rules="规则 Y",
            truth_bundle="状态权威 Z",
            target_word_count=2000,
        )
        c = single_writer_composer()
        user = c.build_user_content(ctx)
        context = c.build_context(ctx)
        # user_content includes outline + meta + requirements
        self.assertIn("第2章", user)
        self.assertIn("主角与女主初次相遇", user)
        self.assertIn("单 Writer 模式", user)
        # context includes truth bundle + character cards + world rules
        self.assertIn("状态权威 Z", context)
        self.assertIn("档案 X", context)
        self.assertIn("规则 Y", context)
        # No spillover between user and context (different roles)
        self.assertNotIn("[角色档案]", user)
        self.assertNotIn("单 Writer 模式", context)

    def test_assembly_uses_performance_records(self) -> None:
        ctx = PromptContext(
            mode="assembly",
            chapter_num=3,
            performance_records=["[节拍1] 张三: 动作"],
            narrator_text="环境描写",
            truth_bundle="状态",
        )
        c = assembly_composer()
        user = c.build_user_content(ctx)
        context = c.build_context(ctx)
        self.assertIn("表演记录", user)
        self.assertIn("节拍1", user)
        self.assertIn("旁白素材", user)
        self.assertNotIn("单 Writer 模式", user)
        self.assertIn("状态", context)

    def test_assembly_empty_performances_warning(self) -> None:
        ctx = PromptContext(mode="assembly", chapter_num=1, performance_records=[])
        user = assembly_composer().build_user_content(ctx)
        self.assertIn("表演记录为空", user)


class TestManifest(unittest.TestCase):

    def test_manifest_shows_per_block_sizes(self) -> None:
        ctx = PromptContext(chapter_num=1, chapter_title="x", outline="abc")
        c = single_writer_composer()
        m = c.render_manifest(ctx)
        # Each manifest entry has name + role + chars + present
        for entry in m:
            self.assertIn("name", entry)
            self.assertIn("role", entry)
            self.assertIn("chars", entry)
        # Header block should be present (chars > 0)
        header = next(e for e in m if e["name"] == "header")
        self.assertGreater(header["chars"], 0)
        self.assertTrue(header["present"])


class TestExtensibility(unittest.TestCase):

    def test_add_custom_provider_via_FnBlock(self) -> None:
        """Demonstrate the value of the abstraction: adding a new block
        requires zero changes to Writer."""
        custom = FnBlock(
            name="customary_greeting", role="user_content",
            fn=lambda ctx: f"## 习俗\n本卷致敬 {ctx.extras.get('homage', '诡秘之主')}",
        )
        c = PromptComposer([HeaderBlock(), custom, OutputRequirementsBlock()])
        ctx = PromptContext(mode="single_writer", chapter_num=1,
                            extras={"homage": "三体"})
        out = c.build_user_content(ctx)
        self.assertIn("习俗", out)
        self.assertIn("三体", out)

    def test_provider_exception_doesnt_kill_composition(self) -> None:
        c = PromptComposer([
            FnBlock(name="ok", role="user_content", fn=lambda _: "ok body"),
            FnBlock(name="broken", role="user_content",
                    fn=lambda _: (_ for _ in ()).throw(RuntimeError("boom"))),
            FnBlock(name="ok2", role="user_content", fn=lambda _: "ok2 body"),
        ])
        # FnBlock catches its own exceptions and returns ""; composition continues
        out = c.build_user_content(PromptContext())
        self.assertIn("ok body", out)
        self.assertIn("ok2 body", out)


if __name__ == "__main__":
    unittest.main()
