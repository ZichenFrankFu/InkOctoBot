"""
Writer — the final-stage prose generator.

Two modes:

  1. **Assembly mode** (``assemble_chapter``) — used by the full
     pipeline. Takes actor performance records + narrator output +
     narrative instructions and stitches them into final prose.

  2. **Single-agent mode** (``write_chapter``) — bypass the multi-agent
     pipeline entirely. Given the chapter outline + character cards +
     world rules + memory context, the Writer produces a complete
     chapter on its own. Useful when:
       - the user wants speed over depth
       - actor models are unavailable / too costly
       - the user already has a tight outline and just needs prose

  Also exposes ``targeted_rewrite`` (used by the rewrite endpoint) and
  ``check_world_rules`` (post-write self-check).

Naming history: this class was called ``EditorWriter`` until v2.1. The
old import path ``agents.production.editor_writer.EditorWriter`` is
re-exported below for backward compat.
"""
from __future__ import annotations

import logging
from typing import Any

from agents.base_agent import BaseAgent
from agents.production.prompt_composer import (
    PromptContext,
    single_writer_composer,
    assembly_composer,
)

logger = logging.getLogger("inkoctobot.agents.production.writer")


class Writer(BaseAgent):
    """Final-stage prose generator. See module docstring for the two modes."""

    # The role key in pipeline config / settings.json stays as
    # ``editor_writer`` so users' existing model-provider mappings keep
    # working. The Python class name is the new ``Writer``.
    agent_name = "editor_writer"

    # ─── Mode 1: Assembly (multi-agent pipeline final stage) ─────────

    async def assemble_chapter(
        self,
        performance_records: list[str],
        narrator_text: str,
        *,
        chapter_num: int = 1,
        chapter_title: str = "",
        narrative_instructions: str = "",
        style_profile: str = "",
        user_preferences: str = "",
        memory_context: str = "",
        constraints: str = "",
        truth_bundle: str = "",
    ) -> str:
        """Assemble raw performances into final chapter text.

        Built around PromptComposer (B1 InkOS) — block ordering, format,
        budgets all live in agents.production.prompt_composer, not here.
        """
        composer = assembly_composer()
        ctx = PromptContext(
            mode="assembly",
            chapter_num=chapter_num,
            chapter_title=chapter_title,
            narrative_instructions=narrative_instructions,
            performance_records=performance_records,
            narrator_text=narrator_text,
            style_profile=style_profile,
            user_preferences=user_preferences,
            memory_context=memory_context,
            truth_bundle=truth_bundle,
            target_word_count=2000,
        )
        user_content = composer.build_user_content(ctx)
        context = composer.build_context(ctx)

        resp = await self.invoke(
            user_content,
            context=context,
            constraints=constraints,
            temperature=0.7,
            max_tokens=8000,
        )

        self.emit_event("GENERATION_STEP_COMPLETED", {
            "step": "chapter_assembly", "chapter_num": chapter_num,
        })
        return resp.content

    # ─── Mode 2: Single-agent (Writer-only) ──────────────────────────

    async def write_chapter(
        self,
        outline: str,
        *,
        chapter_num: int = 1,
        chapter_title: str = "",
        synopsis: str = "",
        time_label: str = "",
        location: str = "",
        characters: list[str] | None = None,
        pov_character: str = "",
        character_cards: str = "",
        world_rules: str = "",
        narrative_instructions: str = "",
        style_profile: str = "",
        user_preferences: str = "",
        memory_context: str = "",
        adjacent_context: str = "",  # deprecated v3.1; kept for back-compat callers
        constraints: str = "",
        target_word_count: int = 2000,
        # InkOS B1 — extra block hooks, optional
        truth_bundle: str = "",
        pressured_hooks_text: str = "",
        ledger_anchors_text: str = "",
    ) -> str:
        """Write a complete chapter from outline + context, no other agents.

        The fast / cheap path. Built around PromptComposer (B1 InkOS) —
        block ordering, format, budgets all live in
        agents.production.prompt_composer.
        """
        composer = single_writer_composer()
        ctx = PromptContext(
            mode="single_writer",
            chapter_num=chapter_num,
            chapter_title=chapter_title,
            outline=outline,
            synopsis=synopsis,
            time_label=time_label,
            location=location,
            characters=characters or [],
            pov_character=pov_character,
            character_cards=character_cards,
            world_rules=world_rules,
            style_profile=style_profile,
            user_preferences=user_preferences,
            memory_context=memory_context,
            adjacent_context=adjacent_context,
            narrative_instructions=narrative_instructions,
            target_word_count=target_word_count,
            # InkOS B1 hooks
            truth_bundle=truth_bundle,
            pressured_hooks_text=pressured_hooks_text,
            ledger_anchors_text=ledger_anchors_text,
        )
        user_content = composer.build_user_content(ctx)
        context = composer.build_context(ctx)

        resp = await self.invoke(
            user_content,
            context=context,
            constraints=constraints,
            temperature=0.7,
            max_tokens=max(4000, target_word_count * 4),
        )

        self.emit_event("GENERATION_STEP_COMPLETED", {
            "step": "writer_only_chapter", "chapter_num": chapter_num,
            "mode": "single_agent",
        })
        return resp.content

    # ─── Auxiliary: world-rules self-check + targeted rewrite ────────

    async def check_world_rules(
        self,
        chapter_text: str,
        world_rules: list[str] | str = "",
        foreshadowing: list[dict] | None = None,
    ) -> list[dict[str, Any]]:
        """Check chapter text against world rules and unresolved foreshadowing.

        Returns list of warnings: [{type, message, severity}].
        """
        rules_str = world_rules if isinstance(world_rules, str) else "\n".join(world_rules)
        if not rules_str and not foreshadowing:
            return []

        foreshadow_str = ""
        if foreshadowing:
            foreshadow_str = "\n未回收伏笔：\n" + "\n".join(
                f"- 第{f.get('chapter', '?')}章：{f.get('text', f.get('description', ''))}"
                for f in foreshadowing
            )

        prompt = f"""请检查以下章节文本是否违反世界观规则或遗忘伏笔。

世界规则：
{rules_str}
{foreshadow_str}

章节正文（前3000字）：
{chapter_text[:3000]}

检查项：
1. 力量体系是否一致
2. 地理/时间是否矛盾
3. 伏笔是否被遗忘或矛盾回收

如果发现问题，以JSON数组格式输出：
[{{"type": "world_rule/foreshadowing/consistency", "message": "问题描述", "severity": "high/medium/low"}}]

如果没有问题，输出空数组：[]
"""
        try:
            resp = await self.invoke(prompt, temperature=0.3, max_tokens=1000)
            import json
            text = resp.content.strip()
            if "[" in text:
                json_str = text[text.index("["):text.rindex("]") + 1]
                warnings = json.loads(json_str)
                return [w for w in warnings if isinstance(w, dict) and w.get("message")]
            return []
        except Exception as e:
            logger.warning("World rules check failed: %s", e)
            return []

    async def targeted_rewrite(
        self,
        original_text: str,
        diagnosis: str,
        *,
        rewrite_scope: str = "paragraph",
        constraints: str = "",
    ) -> str:
        """Rewrite specific problematic sections based on evaluator diagnosis."""
        user_content = (
            f"以下章节文本存在问题，请根据诊断结果进行定向修改。\n"
            f"仅修改问题段落，保持其余部分不变。\n\n"
            f"## 诊断结果\n{diagnosis}\n\n"
            f"## 原文\n{original_text}\n\n"
            f"修改范围: {rewrite_scope}\n"
            f"请输出修改后的完整文本。"
        )
        resp = await self.invoke(
            user_content, constraints=constraints,
            temperature=0.5, max_tokens=8000,
        )
        return resp.content

    # ─── Prompt builder shims (compat with prompt_only endpoint mode) ──
    # Real assembly lives in agents.production.prompt_composer.
    # These wrappers exist so `/api/generation/single-writer?prompt_only=true`
    # can still call them with positional args.

    def _build_assembly_prompt(
        self, performances: list[str], narrator: str,
        chapter_num: int, title: str, instructions: str,
    ) -> str:
        ctx = PromptContext(
            mode="assembly",
            chapter_num=chapter_num, chapter_title=title,
            narrative_instructions=instructions,
            performance_records=performances,
            narrator_text=narrator,
            target_word_count=2000,
        )
        return assembly_composer().build_user_content(ctx)

    def _build_single_writer_prompt(
        self, *, outline: str, chapter_num: int, chapter_title: str,
        synopsis: str, time_label: str, location: str,
        characters: list[str], pov_character: str,
        narrative_instructions: str, target_word_count: int,
    ) -> str:
        ctx = PromptContext(
            mode="single_writer",
            chapter_num=chapter_num, chapter_title=chapter_title,
            outline=outline, synopsis=synopsis,
            time_label=time_label, location=location,
            characters=characters, pov_character=pov_character,
            narrative_instructions=narrative_instructions,
            target_word_count=target_word_count,
        )
        return single_writer_composer().build_user_content(ctx)


# ─── Backward-compat alias ──────────────────────────────────────────
# Older code may still ``from agents.production.editor_writer import EditorWriter``.
# Keep the name resolvable as a deprecated alias.

EditorWriter = Writer
