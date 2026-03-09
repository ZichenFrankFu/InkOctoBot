"""
Actor Agent — role-plays a single character in a scene.

README §2.2.4: Each actor is an independent instance with information
isolation.  Input: scene plan + character card + RAG context + constraints.
Output: semi-structured performance record (actions + dialogue + inner thoughts + atmosphere).
"""
from __future__ import annotations

import logging
from typing import Any

from agents.base_agent import BaseAgent
from agents.model_providers.base import LLMMessage, LLMResponse

logger = logging.getLogger("inkoctobot.agents.production.actor_agent")


class ActorAgent(BaseAgent):
    agent_name = "actor_agent"

    def __init__(self, *args, character_name: str = "", character_card: str = "", **kwargs: Any):
        super().__init__(*args, **kwargs)
        self.character_name = character_name
        self.character_card = character_card

    async def perform(
        self,
        scene_plan: dict[str, Any],
        *,
        scene_context: str = "",
        knowledge_view: str = "",
        previous_beats: str = "",
        constraints: str = "",
    ) -> str:
        """Generate a performance record for this character in the scene."""
        instructions = scene_plan.get("character_instructions", {}).get(self.character_name, {})
        user_content = self._build_performance_prompt(
            scene_plan, instructions, previous_beats,
        )
        context_parts = []
        if self.character_card:
            context_parts.append(f"[角色卡 — {self.character_name}]\n{self.character_card}")
        if knowledge_view:
            context_parts.append(knowledge_view)
        if scene_context:
            context_parts.append(scene_context)
        context = "\n\n".join(context_parts)

        constraint_parts = []
        if constraints:
            constraint_parts.append(constraints)
        musts = instructions.get("must", [])
        must_nots = instructions.get("must_not", [])
        if musts:
            constraint_parts.append("必须: " + "; ".join(musts))
        if must_nots:
            constraint_parts.append("禁止: " + "; ".join(must_nots))

        resp = await self.invoke(
            user_content,
            context=context,
            constraints="\n".join(constraint_parts),
            temperature=0.8,
            max_tokens=3000,
        )
        return resp.content

    def _build_performance_prompt(
        self, scene_plan: dict, instructions: dict, previous_beats: str,
    ) -> str:
        parts = [
            f"你现在是「{self.character_name}」，请以第一人称视角进行角色扮演。",
            f"\n场景: {scene_plan.get('summary', '')}",
            f"地点: {scene_plan.get('location', '未知')}",
            f"时间: {scene_plan.get('time', '')}",
            f"在场人物: {', '.join(scene_plan.get('characters', []))}",
        ]
        if instructions.get("emotional_state"):
            parts.append(f"\n你当前的情绪状态: {instructions['emotional_state']}")
        if instructions.get("secret_goal"):
            parts.append(f"你的秘密目标（不要直接说出）: {instructions['secret_goal']}")
        beats = scene_plan.get("beats", [])
        if beats:
            parts.append(f"\n本场景节拍: {' → '.join(beats)}")
        if previous_beats:
            parts.append(f"\n前序表演:\n{previous_beats}")
        parts.append(f"""
请只输出「{self.character_name}」的表演记录，不要代替其他角色输出。
格式要求：
[节拍N]
{self.character_name}(情绪): *动作描写* "对话内容"
  内心: 内心独白

注意：
- 只输出{self.character_name}的动作、对话和内心独白
- 不要输出其他角色的台词或动作
- 不要输出[氛围]描写（那是旁白的职责）
- 可以在对话中引用其他角色说的话作为反应依据
""")
        return "\n".join(parts)
