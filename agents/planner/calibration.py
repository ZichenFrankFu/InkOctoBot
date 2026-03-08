"""
Style Calibration — generates short sample passages for style confirmation.

README §2.1: Before entering the chapter creation loop, generate sample
fragments so the user can confirm the style direction.
"""
from __future__ import annotations

import logging
from typing import Any

from agents.base_agent import BaseAgent

logger = logging.getLogger("inkoctobot.agents.planner.calibration")


class CalibrationAgent(BaseAgent):
    agent_name = "calibration"

    async def generate_sample(
        self,
        world_book: str,
        character_cards: str,
        outline: str,
        *,
        style_profile: str = "",
        reference_samples: str = "",
        sample_type: str = "opening",
    ) -> str:
        """Generate a short calibration sample (300-500 words)."""
        type_desc = {
            "opening": "小说开篇（第一章前300-500字）",
            "dialogue": "一段对话场景（2-3个角色互动）",
            "action": "一段动作/打斗场景",
            "inner": "一段内心独白/心理描写",
        }.get(sample_type, "一段短文")

        user_content = (
            f"请根据以下设定，生成一段{type_desc}的校准样本。\n\n"
            f"## 世界书摘要\n{world_book[:1500]}\n\n"
            f"## 角色摘要\n{character_cards[:1500]}\n\n"
            f"## 大纲摘要\n{outline[:1000]}"
        )
        context = ""
        if style_profile:
            context += f"[风格要求]\n{style_profile}\n"
        if reference_samples:
            context += f"\n[参考风格片段]\n{reference_samples}"

        resp = await self.invoke(
            user_content, context=context, temperature=0.8, max_tokens=2000,
        )
        return resp.content

    async def generate_variants(
        self, world_book: str, character_cards: str, outline: str,
        *, n_variants: int = 3, style_profile: str = "",
    ) -> list[str]:
        """Generate multiple style variants for comparison."""
        variants = []
        for i in range(n_variants):
            temp = 0.6 + (i * 0.15)  # vary temperature for diversity
            sample = await self.generate_sample(
                world_book, character_cards, outline,
                style_profile=style_profile,
            )
            variants.append(sample)
        return variants
