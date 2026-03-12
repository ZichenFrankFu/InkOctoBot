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

    async def generate_with_feedback(
        self,
        style_params: dict[str, Any],
        feedback_history: list[dict[str, Any]],
        sample_type: str = "opening",
    ) -> dict[str, Any]:
        """Analyze user feedback history and generate an improved sample.

        Input:
            style_params: {tone, pacing, perspective, audience}
            feedback_history: [{sample, score, comment}, ...]
            sample_type: opening/dialogue/action/inner

        Output: {text: str, analysis: str, adjusted_params: dict}
        """
        type_desc = {
            "opening": "小说开篇（300-500字）",
            "dialogue": "一段对话场景",
            "action": "一段动作场景",
            "inner": "一段内心独白",
        }.get(sample_type, "一段短文")

        feedback_context = ""
        if feedback_history:
            feedback_context = "\n\n用户之前的反馈历史：\n"
            for i, fb in enumerate(feedback_history):
                feedback_context += f"样本{i+1}（评分：{fb.get('score', '?')}/5）："
                if fb.get("comment"):
                    feedback_context += f" {fb['comment']}"
                feedback_context += "\n"

        tone_desc = "轻松幽默" if style_params.get("tone", 50) < 30 else "严肃深沉" if style_params.get("tone", 50) > 70 else "均衡"
        pacing_desc = "快节奏" if style_params.get("pacing", 50) < 30 else "慢节奏" if style_params.get("pacing", 50) > 70 else "中等"

        prompt = (
            f"请生成一段{type_desc}的校准样本。\n\n"
            f"风格要求：文风{tone_desc}，{pacing_desc}\n"
            f"{feedback_context}\n\n"
            f"请根据用户反馈调整风格，输出JSON格式：\n"
            f'{{"text": "样本内容", "analysis": "根据反馈做了哪些调整", "adjusted_params": {{}}}}'
        )

        resp = await self.invoke(prompt, temperature=0.8, max_tokens=2000)

        try:
            import json
            result = json.loads(resp.content)
            if isinstance(result, dict) and result.get("text"):
                return result
        except Exception:
            pass

        return {"text": resp.content, "analysis": "", "adjusted_params": {}}

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
