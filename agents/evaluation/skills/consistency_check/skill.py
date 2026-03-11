"""
Consistency Check skill — LLM-based consistency evaluation.

Checks text against character cards, world rules, and previous events
for contradictions and continuity errors.
"""
from __future__ import annotations

import logging
from typing import Any

from agents.base_skill import BaseSkill, SkillMeta

logger = logging.getLogger("inkoctobot.skills.consistency_check")


class Skill(BaseSkill):
    """LLM-based consistency checker."""

    def meta(self) -> SkillMeta:
        return SkillMeta(
            name="consistency_check",
            display_name="一致性检查",
            description=(
                "检查生成文本与角色设定、世界观规则、前情事件的一致性，"
                "通过LLM分析识别不一致之处。"
            ),
            version="1.0.0",
            model_role="evaluator",
            max_tokens=2048,
            temperature=0.3,
            tags=["evaluation", "quality", "consistency"],
            permissions=["read_worldbook", "read_characters"],
            input_schema={
                "type": "object",
                "required": ["text"],
                "properties": {
                    "text": {"type": "string"},
                    "character_cards": {"type": "string", "default": ""},
                    "world_rules": {"type": "string", "default": ""},
                    "previous_events": {"type": "string", "default": ""},
                },
            },
            output_schema={
                "type": "object",
                "required": ["consistent", "issues"],
                "properties": {
                    "consistent": {"type": "boolean"},
                    "issues": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "type": {
                                    "type": "string",
                                    "enum": ["character", "world", "continuity"],
                                },
                                "description": {"type": "string"},
                                "severity": {
                                    "type": "string",
                                    "enum": ["high", "medium", "low"],
                                },
                            },
                        },
                    },
                },
            },
        )

    async def build_prompt(self, inputs: dict[str, Any]) -> str:
        text = inputs["text"]
        character_cards = inputs.get("character_cards", "")
        world_rules = inputs.get("world_rules", "")
        previous_events = inputs.get("previous_events", "")

        prompt = "请检查以下文本是否与已有设定一致。\n\n"
        prompt += f"## 待检查文本\n{text}\n\n"

        if character_cards:
            prompt += f"## 角色设定\n{character_cards}\n\n"
        if world_rules:
            prompt += f"## 世界观规则\n{world_rules}\n\n"
        if previous_events:
            prompt += f"## 前情事件\n{previous_events}\n\n"

        prompt += (
            "请逐条检查以下维度：\n"
            "1. **角色一致性 (character)** — 角色行为、性格、能力是否符合设定\n"
            "2. **世界观一致性 (world)** — 是否违反世界观规则、设定\n"
            "3. **连续性一致性 (continuity)** — 是否与前情事件矛盾\n\n"
            "请严格以JSON格式输出：\n"
            "```json\n"
            "{\n"
            '  "consistent": true/false,\n'
            '  "issues": [\n'
            "    {\n"
            '      "type": "character|world|continuity",\n'
            '      "description": "不一致之处的具体描述",\n'
            '      "severity": "high|medium|low"\n'
            "    }\n"
            "  ]\n"
            "}\n"
            "```\n"
            "如果文本完全一致，issues为空数组。"
        )
        return prompt

    async def parse_output(self, raw: str) -> dict[str, Any]:
        parsed = self.extract_json(raw)
        if parsed and isinstance(parsed, dict):
            return {
                "consistent": parsed.get("consistent", True),
                "issues": parsed.get("issues", []),
            }
        return {"consistent": True, "issues": []}
