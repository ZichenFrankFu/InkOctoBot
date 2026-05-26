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
    ) -> str:
        """Assemble raw performances into final chapter text."""
        user_content = self._build_assembly_prompt(
            performance_records, narrator_text,
            chapter_num, chapter_title, narrative_instructions,
        )
        context_parts = []
        if memory_context:
            context_parts.append(memory_context)
        if style_profile:
            context_parts.append(f"[风格档案]\n{style_profile}")
        if user_preferences:
            context_parts.append(f"[用户偏好]\n{user_preferences}")

        resp = await self.invoke(
            user_content,
            context="\n\n".join(context_parts),
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
        adjacent_context: str = "",
        constraints: str = "",
        target_word_count: int = 2000,
    ) -> str:
        """Write a complete chapter from outline + context, no other agents.

        The fast / cheap path. Skips SceneDirector, ActorAgents, and
        NarratorAgent — the Writer does everything in one LLM call.
        Quality ceiling is lower than the full pipeline, but works well
        when the outline is already detailed.
        """
        user_content = self._build_single_writer_prompt(
            outline=outline,
            chapter_num=chapter_num,
            chapter_title=chapter_title,
            synopsis=synopsis,
            time_label=time_label,
            location=location,
            characters=characters or [],
            pov_character=pov_character,
            narrative_instructions=narrative_instructions,
            target_word_count=target_word_count,
        )
        context_parts: list[str] = []
        if memory_context:
            context_parts.append(memory_context)
        if adjacent_context:
            context_parts.append(adjacent_context)
        if character_cards:
            context_parts.append(f"[角色档案]\n{character_cards}")
        if world_rules:
            context_parts.append(f"[世界规则]\n{world_rules}")
        if style_profile:
            context_parts.append(f"[风格档案]\n{style_profile}")
        if user_preferences:
            context_parts.append(f"[用户偏好]\n{user_preferences}")

        resp = await self.invoke(
            user_content,
            context="\n\n".join(context_parts),
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

    # ─── Prompt builders ─────────────────────────────────────────────

    def _build_assembly_prompt(
        self, performances: list[str], narrator: str,
        chapter_num: int, title: str, instructions: str,
    ) -> str:
        parts = [
            f"请将以下表演记录和旁白素材剪辑成第{chapter_num}章",
        ]
        if title:
            parts[0] += f"「{title}」"
        parts[0] += "的章节正文。"

        if instructions:
            parts.append(f"\n## 叙事指令\n{instructions}")

        real_perfs = [p for p in performances if p and p.strip() and p.strip() not in ("（无表演记录）",)]
        if real_perfs:
            parts.append("\n## 表演记录")
            for i, perf in enumerate(real_perfs):
                parts.append(f"\n--- 场景 {i+1} ---\n{perf}")
        else:
            parts.append("\n## 素材\n（表演记录为空，请根据叙事指令和旁白素材，自行创作章节正文。）")

        if narrator:
            parts.append(f"\n## 旁白素材\n{narrator}")

        parts.append(f"""
## 输出要求
- 将表演记录中的对话、动作、内心独白转化为文学化的叙事文本
- 融入旁白的环境描写和氛围渲染
- 保持情绪弧线的连贯性
- 不要保留表演记录的格式标记
- 目标字数约2000中文字，内容要充实完整
- 直接输出章节正文，从第一个字就是小说正文
- 禁止输出"好的""我明白了""以下是"等确认语、标题或导航链接
""")
        return "\n".join(parts)

    def _build_single_writer_prompt(
        self, *, outline: str, chapter_num: int, chapter_title: str,
        synopsis: str, time_label: str, location: str,
        characters: list[str], pov_character: str,
        narrative_instructions: str, target_word_count: int,
    ) -> str:
        parts = [f"请直接创作第{chapter_num}章"]
        if chapter_title:
            parts[0] += f"「{chapter_title}」"
        parts[0] += "的完整章节正文。"

        meta_lines: list[str] = []
        if synopsis:
            meta_lines.append(f"梗概：{synopsis}")
        if time_label:
            meta_lines.append(f"时间：{time_label}")
        if location:
            meta_lines.append(f"地点：{location}")
        if pov_character:
            meta_lines.append(f"POV 视角：{pov_character}")
        if characters:
            meta_lines.append(f"出场角色：{', '.join(characters)}")
        if meta_lines:
            parts.append("\n## 章节元数据\n" + "\n".join(meta_lines))

        if outline:
            parts.append(f"\n## 章节大纲\n{outline}")

        if narrative_instructions:
            parts.append(f"\n## 叙事指令\n{narrative_instructions}")

        parts.append(f"""
## 输出要求
- 这是**单 Writer 模式**：你需要独立完成场景规划、角色表演、环境描写、剧情推进
- 严格遵循上面的章节大纲，不要漏掉任何关键节点
- 严格遵循 POV 视角，知道与不知道的信息要分清楚
- 严格遵循世界规则和角色设定（见上下文）
- 目标字数约 {target_word_count} 中文字，内容要充实完整
- 直接输出章节正文，从第一个字就是小说正文
- 禁止输出"好的""我明白了""以下是"等确认语、标题或导航链接
- 禁止使用列表/编号语法（- 1. 2.）；用连贯的散文段落
""")
        return "\n".join(parts)


# ─── Backward-compat alias ──────────────────────────────────────────
# Older code may still ``from agents.production.editor_writer import EditorWriter``.
# Keep the name resolvable as a deprecated alias.

EditorWriter = Writer
