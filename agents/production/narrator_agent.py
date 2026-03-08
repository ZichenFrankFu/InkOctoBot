"""
Narrator Agent — handles environment, atmosphere, and non-character narration.

A special "actor" that provides the narrative voice: scene descriptions,
atmosphere rendering, transitions, and sensory details.
"""
from __future__ import annotations

import logging
from typing import Any

from agents.base_agent import BaseAgent

logger = logging.getLogger("inkoctobot.agents.production.narrator_agent")


class NarratorAgent(BaseAgent):
    agent_name = "narrator_agent"

    async def narrate(
        self,
        scene_plan: dict[str, Any],
        *,
        narrator_instructions: str = "",
        scene_context: str = "",
        style_profile: str = "",
        constraints: str = "",
    ) -> str:
        """Generate atmospheric/environmental narration for a scene."""
        user_content = self._build_narration_prompt(
            scene_plan, narrator_instructions,
        )
        context_parts = []
        if scene_context:
            context_parts.append(scene_context)
        if style_profile:
            context_parts.append(f"[风格要求]\n{style_profile}")

        resp = await self.invoke(
            user_content,
            context="\n\n".join(context_parts),
            constraints=constraints,
            temperature=0.8,
            max_tokens=2000,
        )
        return resp.content

    def _build_narration_prompt(self, scene_plan: dict, instructions: str) -> str:
        parts = [
            "你是旁白，负责环境描写、氛围渲染和非角色视角的叙事内容。",
            f"\n场景: {scene_plan.get('summary', '')}",
            f"地点: {scene_plan.get('location', '')}",
            f"时间: {scene_plan.get('time', '')}",
        ]
        if instructions:
            parts.append(f"\n导演指示: {instructions}")
        parts.append("""
请提供:
1. 场景开头的环境描写（100-200字）
2. 关键节拍之间的氛围过渡（每段50-100字）
3. 场景结尾的收束描写（50-100字）

格式:
[开场] 环境描写...
[过渡-节拍N] 氛围过渡...
[收束] 结尾描写...
""")
        return "\n".join(parts)
