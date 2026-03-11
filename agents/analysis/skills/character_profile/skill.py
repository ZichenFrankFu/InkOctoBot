"""Character profile extraction skill — extracts structured character
information from narrative text using LLM analysis."""

from __future__ import annotations

import logging
from typing import Any

from agents.base_skill import BaseSkill, SkillMeta

logger = logging.getLogger("inkoctobot.skills.character_profile")


class Skill(BaseSkill):
    """Extract character profiles (traits, relationships, arcs) from text."""

    def meta(self) -> SkillMeta:
        return SkillMeta(
            name="character_profile",
            display_name="角色画像提取",
            description="从文本中提取角色画像信息，包括性格特征、人物关系、动机、成长弧线等结构化数据。",
            version="1.0.0",
            model_role="analyzer",
            max_tokens=4096,
            temperature=0.3,
            tags=["analysis", "character", "extraction"],
            permissions=["read_characters"],
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
                    "characters": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string"},
                                "aliases": {"type": "array", "items": {"type": "string"}},
                                "appearance": {"type": "string"},
                                "identity": {"type": "string"},
                                "personality": {
                                    "type": "object",
                                    "properties": {
                                        "primary_traits": {"type": "array", "items": {"type": "string"}},
                                        "secondary_traits": {"type": "array", "items": {"type": "string"}},
                                    },
                                },
                                "relationships": {
                                    "type": "array",
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "target": {"type": "string"},
                                            "type": {"type": "string"},
                                            "description": {"type": "string"},
                                        },
                                    },
                                },
                                "motivations": {"type": "array", "items": {"type": "string"}},
                                "arc": {"type": "string"},
                                "signature_quotes": {"type": "array", "items": {"type": "string"}},
                            },
                            "required": ["name"],
                        },
                    },
                },
                "required": ["characters"],
            },
        )

    async def build_prompt(self, inputs: dict[str, Any]) -> str:
        text = inputs["text"]
        return (
            "你是一位专业的文学分析师。请从以下文本中提取所有出场角色的画像信息。\n"
            "\n"
            "分析要求：\n"
            "1. 识别所有出场角色（包括仅被提及的角色）\n"
            "2. 提取每个角色的：姓名、别名、外貌、身份、性格特征（主要/次要）、"
            "人物关系、动机、角色弧线、标志性台词\n"
            "3. 注意分析隐含信息和间接描写\n"
            "4. 关系应双向记录\n"
            "\n"
            "请以如下 JSON 格式输出：\n"
            "```json\n"
            "{\n"
            '  "characters": [\n'
            "    {\n"
            '      "name": "角色名",\n'
            '      "aliases": ["别名"],\n'
            '      "appearance": "外貌描述",\n'
            '      "identity": "身份/职业",\n'
            '      "personality": {\n'
            '        "primary_traits": ["主要性格特征"],\n'
            '        "secondary_traits": ["次要性格特征"]\n'
            "      },\n"
            '      "relationships": [\n'
            '        {"target": "对方角色名", "type": "关系类型", "description": "关系描述"}\n'
            "      ],\n"
            '      "motivations": ["动机"],\n'
            '      "arc": "角色弧线描述",\n'
            '      "signature_quotes": ["标志性台词"]\n'
            "    }\n"
            "  ]\n"
            "}\n"
            "```\n"
            "\n"
            f"待分析文本：\n\n{text}"
        )

    async def parse_output(self, raw: str) -> dict[str, Any]:
        result = self.extract_json(raw)
        if result is None:
            logger.warning("Failed to parse character_profile output, returning empty.")
            return {"characters": []}
        if isinstance(result, list):
            result = {"characters": result}
        if "characters" not in result:
            result = {"characters": []}
        return result
