"""
constraint_disambiguate skill — generates clarification questions for ambiguous constraints.

LLM-based skill wrapping StoryArchitect (Disambiguator) into a BaseSkill-compatible interface.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from agents.base_skill import BaseSkill, SkillMeta

logger = logging.getLogger("inkoctobot.skills.constraint_disambiguate")


class Skill(BaseSkill):
    """LLM-based constraint disambiguation skill."""

    def meta(self) -> SkillMeta:
        return SkillMeta(
            name="constraint_disambiguate",
            display_name="约束消歧",
            description="分析创作设定中的模糊、不完整或矛盾之处，生成针对性的细化问题",
            version="1.0.0",
            model_role="default",
            tags=["planner", "disambiguation", "constraints", "llm"],
            permissions=["read_reference_db"],
            input_schema={
                "type": "object",
                "properties": {
                    "world_book": {"type": "string", "description": "世界书内容"},
                    "character_cards": {"type": "string", "description": "角色卡内容"},
                    "outline": {"type": "string", "description": "大纲内容"},
                    "reference_context": {
                        "type": "string",
                        "default": "",
                        "description": "参考作品信息，用于辅助判断",
                    },
                },
                "required": ["world_book", "character_cards", "outline"],
            },
            output_schema={
                "type": "object",
                "properties": {
                    "analysis": {"type": "string"},
                    "questions": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "category": {"type": "string"},
                                "question": {"type": "string"},
                                "reason": {"type": "string"},
                                "priority": {"type": "string"},
                            },
                        },
                    },
                },
            },
            max_tokens=4000,
            temperature=0.6,
        )

    async def build_prompt(self, inputs: dict[str, Any]) -> str:
        """Build the LLM prompt for generating disambiguation questions."""
        world_book = inputs["world_book"]
        character_cards = inputs["character_cards"]
        outline = inputs["outline"]
        reference_context = inputs.get("reference_context", "")

        prompt = (
            "请分析以下创作设定，识别模糊、不完整或可能矛盾的部分，"
            "并生成针对性的细化问题。\n\n"
            f"## 世界书\n{world_book}\n\n"
            f"## 角色设定\n{character_cards}\n\n"
            f"## 大纲\n{outline}"
        )
        if reference_context:
            prompt += f"\n\n## 参考作品信息\n{reference_context}"

        prompt += (
            "\n\n## 输出要求\n"
            "请以JSON格式输出，包含以下字段：\n"
            "- `analysis`: 对设定问题的整体分析（string）\n"
            "- `questions`: 澄清问题列表（array），每项包含：\n"
            "  - `category`: 问题类别（如「世界观」「角色」「情节」「设定冲突」）\n"
            "  - `question`: 具体的澄清问题\n"
            "  - `reason`: 提出该问题的原因\n"
            "  - `priority`: 优先级（`high` / `medium` / `low`）\n"
        )
        return prompt

    async def parse_output(self, raw: str) -> dict[str, Any]:
        """Parse the LLM output into structured disambiguation result."""
        parsed = self.extract_json(raw)
        if isinstance(parsed, dict):
            return {
                "analysis": parsed.get("analysis", ""),
                "questions": parsed.get("questions", []),
            }
        # Fallback: treat entire output as analysis with no structured questions
        return {"analysis": raw.strip(), "questions": []}
