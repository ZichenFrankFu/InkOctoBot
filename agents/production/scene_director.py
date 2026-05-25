"""
Scene Director — breaks chapter outline into scene plans with director instructions.

README §2.2.3: Takes chapter outline + RAG context + references,
outputs scene breakdown + character instructions + knowledge boundaries.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from agents.base_agent import BaseAgent
from llm.base import LLMResponse

logger = logging.getLogger("inkoctobot.agents.production.scene_director")


class SceneDirector(BaseAgent):
    agent_name = "scene_director"

    async def plan_scenes(
        self,
        chapter_outline: str,
        *,
        chapter_num: int = 1,
        memory_context: str = "",
        world_rules: str = "",
        character_cards: str = "",
        constraints: str = "",
    ) -> dict[str, Any]:
        """Break a chapter outline into a structured scene plan."""
        user_content = self._build_planning_prompt(
            chapter_outline, chapter_num, character_cards,
        )
        context = memory_context
        if world_rules:
            context += f"\n\n[世界观规则]\n{world_rules}"

        resp = await self.invoke(
            user_content, context=context, constraints=constraints,
            temperature=0.6, max_tokens=4000, parse_json=True,
        )
        parsed = resp.raw.get("parsed")
        if not parsed:
            parsed = self._fallback_parse(resp.content, chapter_outline)

        self.emit_event("GENERATION_STEP_COMPLETED", {
            "step": "scene_planning", "chapter_num": chapter_num,
            "scene_count": len(parsed.get("scenes", [])),
        })
        return parsed

    def _build_planning_prompt(self, outline: str, chapter_num: int,
                                character_cards: str) -> str:
        parts = [
            f"请将以下第{chapter_num}章细纲拆解为分镜场景计划。",
            f"\n## 章节细纲\n{outline}",
        ]
        if character_cards:
            parts.append(f"\n## 相关角色卡\n{character_cards}")
        parts.append("""
【重要】只规划本章内容，不要引入其他章节的角色或剧情。场景中的 characters 数组只能使用上方「相关角色卡」中列出的角色名。

请以JSON格式输出场景计划：
```json
{
  "chapter_num": N,
  "scenes": [
    {
      "scene_index": 0,
      "location": "地点",
      "time": "时间标记",
      "characters": ["仅限上方列出的角色"],
      "summary": "场景概要",
      "beats": ["节拍1", "节拍2"],
      "character_instructions": {
        "角色名": {
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

    @staticmethod
    def _fallback_parse(content: str, outline: str) -> dict[str, Any]:
        """Fallback when JSON parsing fails — create a single-scene plan."""
        return {
            "scenes": [{
                "scene_index": 0,
                "location": "",
                "time": "",
                "characters": [],
                "summary": outline[:200],
                "beats": [outline],
                "character_instructions": {},
                "narrator_instructions": content[:500] if content else "",
            }],
            "chapter_arc": "",
        }
