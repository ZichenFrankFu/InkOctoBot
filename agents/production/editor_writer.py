"""
Editor-Writer — assembles performance records into polished chapter text.

README §2.2.5: Takes actor performance records + narrator output + narrative
instructions (POV, pacing, emotional arc) + style requirements, and produces
the final chapter prose.
"""
from __future__ import annotations

import logging
from typing import Any

from agents.base_agent import BaseAgent

logger = logging.getLogger("inkoctobot.agents.production.editor_writer")


class EditorWriter(BaseAgent):
    agent_name = "editor_writer"

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

        # Filter out empty or placeholder performances
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
