"""
Tests for the Writer agent (renamed from EditorWriter).

Two modes covered:
  - assemble_chapter (multi-agent pipeline final stage)
  - write_chapter   (single-agent mode — new in v2.1)

Plus:
  - EditorWriter import alias still works
  - agent_name stays as 'editor_writer' so existing pipeline configs
    in settings.json don't break
"""
from __future__ import annotations

import asyncio
import os
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))

from agents.production.writer import Writer, EditorWriter


class _Resp:
    def __init__(self, content: str) -> None:
        self.content = content
        self.model = "mock"
        self.input_tokens = 10
        self.output_tokens = 100


def _mk_writer() -> tuple[Writer, MagicMock]:
    router = MagicMock()
    router.generate = AsyncMock(return_value=_Resp("生成的章节正文。"))
    writer = Writer(router, project_id="p1")
    return writer, router


class TestRename(unittest.TestCase):

    def test_editor_writer_alias_works(self) -> None:
        """Old code path ``from agents.production.editor_writer import EditorWriter``."""
        from agents.production.editor_writer import EditorWriter as Legacy
        self.assertIs(Legacy, Writer)
        # And the direct alias on the new module:
        self.assertIs(EditorWriter, Writer)

    def test_agent_name_stays_editor_writer_for_config_compat(self) -> None:
        """User settings.json maps `pipeline.editor_writer` to a provider;
        renaming the agent_name would silently break that mapping."""
        writer, _ = _mk_writer()
        self.assertEqual(writer.agent_name, "editor_writer")


class TestAssembleChapter(unittest.IsolatedAsyncioTestCase):

    async def test_calls_router_and_returns_text(self) -> None:
        writer, router = _mk_writer()
        text = await writer.assemble_chapter(
            performance_records=["[节拍1] 张三: *动作* \"对话\""],
            narrator_text="环境描写",
            chapter_num=1,
            chapter_title="开端",
            narrative_instructions="POV: 张三",
        )
        self.assertIn("生成的章节正文", text)
        router.generate.assert_called_once()
        call = router.generate.call_args
        # routing key is editor_writer
        self.assertEqual(call.kwargs.get("agent_role"), "editor_writer")

    async def test_empty_performances_dont_crash(self) -> None:
        writer, _ = _mk_writer()
        text = await writer.assemble_chapter(
            performance_records=[],
            narrator_text="",
            chapter_num=1,
        )
        self.assertTrue(text)


class TestSingleWriterMode(unittest.IsolatedAsyncioTestCase):

    async def test_write_chapter_uses_single_writer_prompt(self) -> None:
        writer, router = _mk_writer()
        text = await writer.write_chapter(
            outline="第一章大纲：主角发现奇怪的水晶",
            chapter_num=1,
            chapter_title="意外发现",
            pov_character="李星河",
            characters=["李星河", "苏晚"],
            character_cards="[档案] 李星河...",
            world_rules="星门已知规则...",
            target_word_count=1500,
        )
        self.assertIn("生成的章节正文", text)
        router.generate.assert_called_once()

        # Inspect the prompt sent to the LLM — it should contain
        # single-writer-mode markers, not assembly markers.
        msgs = router.generate.call_args.kwargs["messages"]
        user_msg = next(m for m in msgs if m.role == "user").content
        self.assertIn("第1章", user_msg)
        self.assertIn("意外发现", user_msg)
        self.assertIn("单 Writer 模式", user_msg)
        self.assertIn("李星河", user_msg)
        # NOT assembly markers
        self.assertNotIn("表演记录", user_msg)

    async def test_max_tokens_scales_with_target_word_count(self) -> None:
        writer, router = _mk_writer()
        await writer.write_chapter(outline="x", target_word_count=5000)
        self.assertEqual(
            router.generate.call_args.kwargs.get("max_tokens"),
            20000,  # max(4000, 5000*4)
        )

    async def test_emits_step_event_with_single_agent_mode_tag(self) -> None:
        writer, _ = _mk_writer()
        # Capture emitted events
        emitted: list[tuple[str, dict]] = []
        writer.emit_event = lambda ev, payload: emitted.append((ev, payload))  # type: ignore
        await writer.write_chapter(outline="x", chapter_num=7)
        self.assertEqual(len(emitted), 1)
        ev, payload = emitted[0]
        self.assertEqual(ev, "GENERATION_STEP_COMPLETED")
        self.assertEqual(payload["mode"], "single_agent")
        self.assertEqual(payload["chapter_num"], 7)


if __name__ == "__main__":
    unittest.main()
