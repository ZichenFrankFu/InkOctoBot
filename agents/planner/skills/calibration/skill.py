"""
calibration skill — generates sample passages for style confirmation.

LLM-based skill wrapping CalibrationAgent into a BaseSkill-compatible interface.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from agents.base_skill import BaseSkill, SkillMeta

logger = logging.getLogger("inkoctobot.skills.calibration")

_SAMPLE_TYPE_DESC = {
    "opening": "小说开篇（第一章前300-500字）",
    "dialogue": "一段对话场景（2-3个角色互动）",
    "action": "一段动作/打斗场景",
    "inner": "一段内心独白/心理描写",
}


class Skill(BaseSkill):
    """LLM-based style calibration skill."""

    def meta(self) -> SkillMeta:
        return SkillMeta(
            name="calibration",
            display_name="风格校准",
            description="根据创作设定生成短篇校准样本，用于风格方向确认",
            version="1.0.0",
            model_role="default",
            tags=["planner", "calibration", "style", "llm"],
            permissions=["read_reference_db"],
            input_schema={
                "type": "object",
                "properties": {
                    "world_book": {"type": "string", "description": "世界书内容"},
                    "character_cards": {"type": "string", "description": "角色卡内容"},
                    "outline": {"type": "string", "description": "大纲内容"},
                    "sample_type": {
                        "type": "string",
                        "enum": ["opening", "dialogue", "action", "inner"],
                        "default": "opening",
                        "description": "样本类型",
                    },
                    "style_profile": {"type": "string", "default": "", "description": "风格要求"},
                    "reference_samples": {"type": "string", "default": "", "description": "参考风格片段"},
                    "n_variants": {"type": "integer", "default": 1, "description": "变体数量"},
                },
                "required": ["world_book", "character_cards", "outline"],
            },
            output_schema={
                "type": "object",
                "properties": {
                    "samples": {"type": "array", "items": {"type": "string"}},
                    "sample_type": {"type": "string"},
                    "n_variants": {"type": "integer"},
                },
            },
            max_tokens=2000,
            temperature=0.8,
        )

    async def build_prompt(self, inputs: dict[str, Any]) -> str:
        """Build the LLM prompt for generating a calibration sample."""
        sample_type = inputs.get("sample_type", "opening")
        type_desc = _SAMPLE_TYPE_DESC.get(sample_type, "一段短文")
        style_profile = inputs.get("style_profile", "")
        reference_samples = inputs.get("reference_samples", "")

        prompt = (
            f"请根据以下设定，生成一段{type_desc}的校准样本。\n\n"
            f"## 世界书摘要\n{inputs['world_book'][:1500]}\n\n"
            f"## 角色摘要\n{inputs['character_cards'][:1500]}\n\n"
            f"## 大纲摘要\n{inputs['outline'][:1000]}"
        )
        if style_profile:
            prompt += f"\n\n## 风格要求\n{style_profile}"
        if reference_samples:
            prompt += f"\n\n## 参考风格片段\n{reference_samples}"

        prompt += (
            "\n\n## 要求\n"
            "- 300-500字\n"
            "- 体现上述设定的世界观和角色特征\n"
            "- 直接输出样本正文，不要添加标题或解释"
        )
        return prompt

    async def parse_output(self, raw: str) -> dict[str, Any]:
        """Parse the LLM output into structured result."""
        # The LLM returns prose text directly; wrap into structured output
        sample_text = raw.strip()
        return {
            "samples": [sample_text],
            "sample_type": "opening",
            "n_variants": 1,
        }

    async def execute(
        self,
        inputs: dict[str, Any],
        model_router: Any,
    ) -> dict[str, Any]:
        """Execute calibration — supports multi-variant generation."""
        n_variants = inputs.get("n_variants", 1)
        sample_type = inputs.get("sample_type", "opening")

        if n_variants <= 1:
            # Single sample: use standard flow
            result = await super().execute(inputs, model_router)
            result["sample_type"] = sample_type
            result["n_variants"] = 1
            return result

        # Multiple variants: vary temperature for diversity
        samples: list[str] = []
        base_temp = self.meta().temperature
        for i in range(n_variants):
            temp = 0.6 + (i * 0.15)
            prompt = await self.build_prompt(inputs)
            raw = await model_router.invoke(
                role=self.meta().model_role,
                prompt=prompt,
                max_tokens=self.meta().max_tokens,
                temperature=temp,
            )
            samples.append(raw.strip())

        return {
            "samples": samples,
            "sample_type": sample_type,
            "n_variants": n_variants,
        }
