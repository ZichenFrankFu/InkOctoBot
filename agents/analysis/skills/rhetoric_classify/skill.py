"""Rhetoric classification skill — identifies and classifies rhetorical
devices in text using LLM analysis."""

from __future__ import annotations

import logging
from typing import Any

from agents.base_skill import BaseSkill, SkillMeta

logger = logging.getLogger("inkoctobot.skills.rhetoric_classify")


class Skill(BaseSkill):
    """Classify rhetorical devices (metaphor, parallelism, irony, etc.) in text."""

    def meta(self) -> SkillMeta:
        return SkillMeta(
            name="rhetoric_classify",
            display_name="修辞手法分类",
            description="识别并分类文本中的修辞手法，包括比喻、排比、拟人、夸张、反讽等。",
            version="1.0.0",
            model_role="analyzer",
            max_tokens=4096,
            temperature=0.3,
            tags=["analysis", "rhetoric", "classification"],
            permissions=["read_rhetoric"],
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
                    "devices": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "type": {"type": "string"},
                                "subtype": {"type": "string"},
                                "text_span": {"type": "string"},
                                "explanation": {"type": "string"},
                                "effectiveness": {
                                    "type": "string",
                                    "enum": ["high", "medium", "low"],
                                },
                            },
                            "required": ["type", "text_span", "explanation"],
                        },
                    },
                    "summary": {
                        "type": "object",
                        "properties": {
                            "total_count": {"type": "integer"},
                            "type_distribution": {"type": "object"},
                            "overall_style": {"type": "string"},
                        },
                    },
                },
                "required": ["devices", "summary"],
            },
        )

    async def build_prompt(self, inputs: dict[str, Any]) -> str:
        text = inputs["text"]
        return (
            "你是一位专业的修辞学分析师。请从以下文本中识别并分类所有修辞手法。\n"
            "\n"
            "分析要求：\n"
            "1. 识别所有修辞手法，包括：比喻（明喻/暗喻/借喻）、排比、对仗、"
            "拟人、拟物、夸张、反讽、讽刺、双关、暗示、反复、回环、设问、"
            "反问、通感、移觉等\n"
            "2. 对每个修辞手法，标注原文片段、类型、效果分析\n"
            "3. 评估每个修辞手法的有效性（high/medium/low）\n"
            "4. 注意识别嵌套和复合修辞结构\n"
            "5. 提供整体修辞风格总结\n"
            "\n"
            "请以如下 JSON 格式输出：\n"
            "```json\n"
            "{\n"
            '  "devices": [\n'
            "    {\n"
            '      "type": "修辞类型",\n'
            '      "subtype": "子类型（如有，否则为null）",\n'
            '      "text_span": "原文片段",\n'
            '      "explanation": "修辞效果分析",\n'
            '      "effectiveness": "high | medium | low"\n'
            "    }\n"
            "  ],\n"
            '  "summary": {\n'
            '    "total_count": 0,\n'
            '    "type_distribution": {"比喻": 3, "排比": 2},\n'
            '    "overall_style": "整体修辞风格评价"\n'
            "  }\n"
            "}\n"
            "```\n"
            "\n"
            f"待分析文本：\n\n{text}"
        )

    async def parse_output(self, raw: str) -> dict[str, Any]:
        result = self.extract_json(raw)
        if result is None:
            logger.warning("Failed to parse rhetoric_classify output, returning empty.")
            return {
                "devices": [],
                "summary": {
                    "total_count": 0,
                    "type_distribution": {},
                    "overall_style": "",
                },
            }
        if "devices" not in result:
            result["devices"] = []
        if "summary" not in result:
            # Build summary from devices
            type_dist: dict[str, int] = {}
            for device in result["devices"]:
                t = device.get("type", "unknown")
                type_dist[t] = type_dist.get(t, 0) + 1
            result["summary"] = {
                "total_count": len(result["devices"]),
                "type_distribution": type_dist,
                "overall_style": "",
            }
        return result
