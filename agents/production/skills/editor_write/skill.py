"""
editor_write — assembles performance records into polished chapter text.
"""
from __future__ import annotations

from typing import Any

from agents.base_skill import BaseSkill, SkillMeta


class Skill(BaseSkill):
    """Editor Write skill: performance records + narrator -> chapter prose."""

    def meta(self) -> SkillMeta:
        return SkillMeta(
            name="editor_write",
            display_name="编辑写作",
            description="将角色表演记录和旁白素材剪辑组装为文学化的章节正文",
            version="1.0.0",
            model_role="editor_writer",
            max_tokens=8000,
            temperature=0.7,
            tags=["production", "writing", "assembly"],
            permissions=["read_worldbook", "read_characters"],
            input_schema={
                "type": "object",
                "required": ["performance_records", "narrator_text", "chapter_num"],
                "properties": {
                    "performance_records": {"type": "array", "items": {"type": "string"}},
                    "narrator_text": {"type": "string"},
                    "chapter_num": {"type": "integer"},
                    "narrative_instructions": {"type": "string"},
                    "style_profile": {"type": "string"},
                },
            },
            output_schema={
                "type": "object",
                "required": ["text"],
                "properties": {
                    "text": {"type": "string"},
                },
            },
        )

    async def build_prompt(self, inputs: dict[str, Any]) -> str:
        performance_records: list[str] = inputs["performance_records"]
        narrator_text: str = inputs["narrator_text"]
        chapter_num: int = inputs["chapter_num"]
        narrative_instructions: str = inputs.get("narrative_instructions", "")
        style_profile: str = inputs.get("style_profile", "")

        parts = [
            f"请将以下表演记录和旁白素材剪辑成第{chapter_num}章的章节正文。",
        ]

        if style_profile:
            parts.append(f"\n## 风格档案\n{style_profile}")

        if narrative_instructions:
            parts.append(f"\n## 叙事指令\n{narrative_instructions}")

        parts.append("\n## 表演记录")
        for i, perf in enumerate(performance_records):
            parts.append(f"\n--- 场景 {i + 1} ---\n{perf}")

        if narrator_text:
            parts.append(f"\n## 旁白素材\n{narrator_text}")

        parts.append("""
## 输出要求
- 将表演记录中的对话、动作、内心独白转化为文学化的叙事文本
- 融入旁白的环境描写和氛围渲染
- 保持情绪弧线的连贯性
- 不要保留表演记录的格式标记
- 直接输出章节正文，不需要额外说明
""")
        return "\n".join(parts)

    async def parse_output(self, raw: str) -> dict[str, Any]:
        return {"text": raw}
