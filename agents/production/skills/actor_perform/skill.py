"""
actor_perform — role-plays a single character in a scene.
"""
from __future__ import annotations

from typing import Any

from agents.base_skill import BaseSkill, SkillMeta


class Skill(BaseSkill):
    """Actor Perform skill: scene plan + character -> performance text."""

    def meta(self) -> SkillMeta:
        return SkillMeta(
            name="actor_perform",
            display_name="角色扮演",
            description="以第一人称视角扮演单个角色，生成包含动作、对话和内心独白的表演记录",
            version="1.0.0",
            model_role="actor_agent",
            max_tokens=3000,
            temperature=0.8,
            tags=["production", "acting", "character"],
            permissions=["read_worldbook", "read_characters"],
            input_schema={
                "type": "object",
                "required": ["scene_plan", "character_name", "character_card"],
                "properties": {
                    "scene_plan": {"type": "object"},
                    "character_name": {"type": "string"},
                    "character_card": {"type": "string"},
                    "knowledge_view": {"type": "string"},
                    "previous_beats": {"type": "string"},
                },
            },
            output_schema={
                "type": "object",
                "required": ["text"],
                "properties": {
                    "text": {"type": "string"},
                },
            },
        )

    async def build_prompt(self, inputs: dict[str, Any]) -> str:
        scene_plan: dict[str, Any] = inputs["scene_plan"]
        character_name: str = inputs["character_name"]
        character_card: str = inputs["character_card"]
        knowledge_view: str = inputs.get("knowledge_view", "")
        previous_beats: str = inputs.get("previous_beats", "")

        instructions = scene_plan.get("character_instructions", {}).get(character_name, {})

        parts = [
            f"你现在是「{character_name}」，请以第一人称视角进行角色扮演。",
        ]
        if character_card:
            parts.append(f"\n## 角色卡\n{character_card}")
        if knowledge_view:
            parts.append(f"\n## 可见知识\n{knowledge_view}")

        parts.extend([
            f"\n## 场景信息",
            f"场景: {scene_plan.get('summary', '')}",
            f"地点: {scene_plan.get('location', '未知')}",
            f"时间: {scene_plan.get('time', '')}",
            f"在场人物: {', '.join(scene_plan.get('characters', []))}",
        ])

        if instructions.get("emotional_state"):
            parts.append(f"\n你当前的情绪状态: {instructions['emotional_state']}")
        if instructions.get("secret_goal"):
            parts.append(f"你的秘密目标（不要直接说出）: {instructions['secret_goal']}")

        musts = instructions.get("must", [])
        must_nots = instructions.get("must_not", [])
        if musts:
            parts.append(f"\n必须: {'; '.join(musts)}")
        if must_nots:
            parts.append(f"禁止: {'; '.join(must_nots)}")

        beats = scene_plan.get("beats", [])
        if beats:
            parts.append(f"\n本场景节拍: {' → '.join(beats)}")

        if previous_beats:
            parts.append(f"\n## 前序表演\n{previous_beats}")

        parts.append(f"""
## 输出要求
请只输出「{character_name}」的表演记录，不要代替其他角色输出。
格式：
[节拍N]
{character_name}(情绪): *动作描写* "对话内容"
  内心: 内心独白

注意：
- 只输出{character_name}的动作、对话和内心独白
- 不要输出其他角色的台词或动作
- 不要输出[氛围]描写（那是旁白的职责）
- 可以在对话中引用其他角色说的话作为反应依据
""")
        return "\n".join(parts)

    async def parse_output(self, raw: str) -> dict[str, Any]:
        return {"text": raw}
