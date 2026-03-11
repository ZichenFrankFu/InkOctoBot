"""Shuangdian (satisfaction/exciting points) extraction skill — identifies
and classifies 爽点 in web novel text using LLM analysis."""

from __future__ import annotations

import logging
from typing import Any

from agents.base_skill import BaseSkill, SkillMeta

logger = logging.getLogger("inkoctobot.skills.shuangdian_extract")


class Skill(BaseSkill):
    """Extract shuangdian (爽点) types and locations from web novel text."""

    def meta(self) -> SkillMeta:
        return SkillMeta(
            name="shuangdian_extract",
            display_name="爽点提取",
            description="从网文文本中提取爽点（满足感/兴奋点），包括爽点类型、位置、强度及触发机制。",
            version="1.0.0",
            model_role="analyzer",
            max_tokens=4096,
            temperature=0.3,
            tags=["analysis", "shuangdian", "extraction", "webnovel"],
            permissions=["read_narrative"],
            input_schema={
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "待分析的网文文本"},
                },
                "required": ["text"],
            },
            output_schema={
                "type": "object",
                "properties": {
                    "shuangdian": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "type": {"type": "string"},
                                "subtype": {"type": "string"},
                                "text_span": {"type": "string"},
                                "position": {"type": "string"},
                                "intensity": {
                                    "type": "string",
                                    "enum": ["high", "medium", "low"],
                                },
                                "trigger": {"type": "string"},
                                "emotional_payoff": {"type": "string"},
                            },
                            "required": ["type", "text_span", "intensity", "trigger"],
                        },
                    },
                    "summary": {
                        "type": "object",
                        "properties": {
                            "total_count": {"type": "integer"},
                            "type_distribution": {"type": "object"},
                            "pacing_assessment": {"type": "string"},
                            "density_score": {"type": "number"},
                        },
                    },
                },
                "required": ["shuangdian", "summary"],
            },
        )

    async def build_prompt(self, inputs: dict[str, Any]) -> str:
        text = inputs["text"]
        return (
            "你是一位资深的网络小说分析师，擅长识别网文中的爽点结构。\n"
            "请从以下文本中提取所有爽点（读者满足感/兴奋点）。\n"
            "\n"
            "爽点类型包括但不限于：\n"
            "- 打脸（face_slapping）：主角反击轻视者、啪啪打脸\n"
            "- 升级（power_up）：实力突破、境界提升、获得宝物/传承\n"
            "- 装逼（show_off）：展示隐藏实力、震惊全场\n"
            "- 复仇（revenge）：以牙还牙、惩罚反派/恶人\n"
            "- 逆袭（comeback）：绝境翻盘、扭转乾坤\n"
            "- 认亲（identity_reveal）：身份揭露、贵人相认\n"
            "- 碾压（domination）：以绝对实力碾压对手\n"
            "- 收获（harvest）：大量获取资源、意外奖励\n"
            "\n"
            "分析要求：\n"
            "1. 标注每个爽点的原文片段、类型、强度（high/medium/low）\n"
            "2. 分析触发机制（是什么铺垫让读者产生爽感）\n"
            "3. 描述读者的情感回报（emotional_payoff）\n"
            "4. 标注大致位置（文本百分比）\n"
            "5. 评价整体爽点节奏和密度\n"
            "\n"
            "请以如下 JSON 格式输出：\n"
            "```json\n"
            "{\n"
            '  "shuangdian": [\n'
            "    {\n"
            '      "type": "爽点类型（如 face_slapping, power_up, show_off 等）",\n'
            '      "subtype": "子类型（如有，否则为null）",\n'
            '      "text_span": "原文片段",\n'
            '      "position": "百分比位置",\n'
            '      "intensity": "high | medium | low",\n'
            '      "trigger": "触发机制描述",\n'
            '      "emotional_payoff": "读者情感回报描述"\n'
            "    }\n"
            "  ],\n"
            '  "summary": {\n'
            '    "total_count": 0,\n'
            '    "type_distribution": {"打脸": 2, "升级": 1},\n'
            '    "pacing_assessment": "爽点节奏评价",\n'
            '    "density_score": 0.0\n'
            "  }\n"
            "}\n"
            "```\n"
            "\n"
            f"待分析文本：\n\n{text}"
        )

    async def parse_output(self, raw: str) -> dict[str, Any]:
        result = self.extract_json(raw)
        if result is None:
            logger.warning("Failed to parse shuangdian_extract output, returning empty.")
            return {
                "shuangdian": [],
                "summary": {
                    "total_count": 0,
                    "type_distribution": {},
                    "pacing_assessment": "",
                    "density_score": 0.0,
                },
            }
        if "shuangdian" not in result:
            result["shuangdian"] = []
        if "summary" not in result:
            # Build summary from shuangdian list
            type_dist: dict[str, int] = {}
            for item in result["shuangdian"]:
                t = item.get("type", "unknown")
                type_dist[t] = type_dist.get(t, 0) + 1
            result["summary"] = {
                "total_count": len(result["shuangdian"]),
                "type_distribution": type_dist,
                "pacing_assessment": "",
                "density_score": 0.0,
            }
        return result
