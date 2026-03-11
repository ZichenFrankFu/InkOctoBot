"""
scene_direct — breaks chapter outline into scene plans with director instructions.
"""
from __future__ import annotations

from typing import Any

from agents.base_skill import BaseSkill, SkillMeta


class Skill(BaseSkill):
    """Scene Director skill: chapter outline -> structured scene plan (JSON)."""

    def meta(self) -> SkillMeta:
        return SkillMeta(
            name="scene_direct",
            display_name="场景导演",
            description="将章节细纲拆解为分镜场景计划，包含角色指令、知识边界和情绪弧线",
            version="1.0.0",
            model_role="scene_director",
            max_tokens=4000,
            temperature=0.6,
            tags=["production", "scene", "planning"],
            permissions=["read_worldbook", "read_characters"],
            input_schema={
                "type": "object",
                "required": ["chapter_outline", "chapter_num"],
                "properties": {
                    "chapter_outline": {"type": "string"},
                    "chapter_num": {"type": "integer"},
                    "character_cards": {"type": "string"},
                },
            },
            output_schema={
                "type": "object",
                "required": ["scenes"],
                "properties": {
                    "chapter_num": {"type": "integer"},
                    "scenes": {"type": "array"},
                    "chapter_arc": {"type": "string"},
                },
            },
        )

    async def build_prompt(self, inputs: dict[str, Any]) -> str:
        chapter_outline: str = inputs["chapter_outline"]
        chapter_num: int = inputs["chapter_num"]
        character_cards: str = inputs.get("character_cards", "")

        parts = [
            f"请将以下第{chapter_num}章细纲拆解为分镜场景计划。",
            f"\n## 章节细纲\n{chapter_outline}",
        ]
        if character_cards:
            parts.append(f"\n## 相关角色卡\n{character_cards}")
        parts.append("""
请以JSON格式输出场景计划：
```json
{
  "chapter_num": N,
  "scenes": [
    {
      "scene_index": 0,
      "location": "地点",
      "time": "时间标记",
      "characters": ["角色A", "角色B"],
      "summary": "场景概要",
      "beats": ["节拍1", "节拍2"],
      "character_instructions": {
        "角色A": {
          "emotional_state": "情绪状态",
          "secret_goal": "秘密目标",
          "knowledge_boundary": {
            "restricted_facts": ["角色不知道的信息"],
            "must_not_know": ["禁止暗示的信息"]
          },
          "must": ["必须做的事"],
          "must_not": ["不能做的事"]
        }
      },
      "narrator_instructions": "旁白描写指引"
    }
  ],
  "chapter_arc": "本章情绪弧线描述"
}
```""")
        return "\n".join(parts)

    async def parse_output(self, raw: str) -> dict[str, Any]:
        parsed = self.extract_json(raw)
        if parsed and isinstance(parsed, dict):
            return parsed
        # Fallback: return a minimal single-scene plan
        return {
            "scenes": [{
                "scene_index": 0,
                "location": "",
                "time": "",
                "characters": [],
                "summary": "",
                "beats": [],
                "character_instructions": {},
                "narrator_instructions": raw[:500] if raw else "",
            }],
            "chapter_arc": "",
        }
