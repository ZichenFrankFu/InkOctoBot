"""Narrative element extraction skill — extracts plot points, arcs,
tension curves, and conflict structures from text using LLM analysis."""

from __future__ import annotations

import logging
from typing import Any

from agents.base_skill import BaseSkill, SkillMeta

logger = logging.getLogger("inkoctobot.skills.narrative_extract")


class Skill(BaseSkill):
    """Extract narrative elements (plot points, arcs, tension, conflicts) from text."""

    def meta(self) -> SkillMeta:
        return SkillMeta(
            name="narrative_extract",
            display_name="叙事要素提取",
            description="从文本中提取叙事要素，包括情节点、叙事弧线、张力曲线、冲突结构等。",
            version="1.0.0",
            model_role="analyzer",
            max_tokens=4096,
            temperature=0.3,
            tags=["analysis", "narrative", "extraction"],
            permissions=["read_narrative"],
            input_schema={
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "待分析的文本内容"},
                },
                "required": ["text"],
            },
            output_schema={
                "type": "object",
                "properties": {
                    "plot_points": {"type": "array"},
                    "narrative_arc": {"type": "object"},
                    "tension_curve": {"type": "array"},
                    "conflicts": {"type": "array"},
                    "hooks": {"type": "array"},
                    "foreshadowing": {"type": "array"},
                },
                "required": ["plot_points", "narrative_arc", "tension_curve", "conflicts"],
            },
        )

    async def build_prompt(self, inputs: dict[str, Any]) -> str:
        text = inputs["text"]
        return (
            "你是一位专业的叙事学分析师。请从以下文本中提取核心叙事要素。\n"
            "\n"
            "分析要求：\n"
            "1. 识别所有关键情节点（inciting_incident, rising_action, climax, "
            "falling_action, resolution）\n"
            "2. 判断整体叙事弧线结构（三幕式、起承转合、英雄之旅等）\n"
            "3. 绘制张力曲线：在文本不同位置（0-100%）标注紧张度（1-10）\n"
            "4. 分析冲突结构：类型（internal/interpersonal/societal/environmental）、"
            "冲突方、状态\n"
            "5. 识别悬念（hooks）和伏笔（foreshadowing）\n"
            "\n"
            "请以如下 JSON 格式输出：\n"
            "```json\n"
            "{\n"
            '  "plot_points": [\n'
            '    {"event": "事件描述", "type": "类型", "position": "百分比", '
            '"significance": "重要性说明"}\n'
            "  ],\n"
            '  "narrative_arc": {\n'
            '    "structure": "结构类型",\n'
            '    "stages": [{"stage": "阶段名", "summary": "概要"}]\n'
            "  },\n"
            '  "tension_curve": [\n'
            '    {"position": "0-100", "level": "1-10", "reason": "原因"}\n'
            "  ],\n"
            '  "conflicts": [\n'
            '    {"type": "类型", "parties": ["冲突方"], "description": "描述", '
            '"status": "状态"}\n'
            "  ],\n"
            '  "hooks": ["悬念描述"],\n'
            '  "foreshadowing": ["伏笔描述"]\n'
            "}\n"
            "```\n"
            "\n"
            f"待分析文本：\n\n{text}"
        )

    async def parse_output(self, raw: str) -> dict[str, Any]:
        result = self.extract_json(raw)
        if result is None:
            logger.warning("Failed to parse narrative_extract output, returning empty.")
            return {
                "plot_points": [],
                "narrative_arc": {"structure": "unknown", "stages": []},
                "tension_curve": [],
                "conflicts": [],
                "hooks": [],
                "foreshadowing": [],
            }
        # Ensure all expected keys exist
        defaults = {
            "plot_points": [],
            "narrative_arc": {"structure": "unknown", "stages": []},
            "tension_curve": [],
            "conflicts": [],
            "hooks": [],
            "foreshadowing": [],
        }
        for key, default in defaults.items():
            if key not in result:
                result[key] = default
        return result
