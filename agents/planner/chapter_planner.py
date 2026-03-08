"""
Chapter Planner — helps users develop detailed chapter outlines.

Works with Story Architect's global outline to develop per-chapter
fine-grained outlines that feed into the Scene Director.
"""
from __future__ import annotations

import logging
from typing import Any

from agents.base_agent import BaseAgent

logger = logging.getLogger("inkoctobot.agents.planner.chapter_planner")


class ChapterPlanner(BaseAgent):
    agent_name = "chapter_planner"

    async def plan_chapter(
        self,
        volume_outline: str,
        chapter_num: int,
        *,
        previous_summary: str = "",
        character_states: str = "",
        unresolved_threads: str = "",
        constraints: str = "",
    ) -> dict[str, Any]:
        """Generate a detailed chapter outline."""
        user_content = (
            f"请为第{chapter_num}章制定详细的章节细纲。\n\n"
            f"## 分卷大纲\n{volume_outline}\n\n"
        )
        if previous_summary:
            user_content += f"## 前情提要\n{previous_summary}\n\n"
        if character_states:
            user_content += f"## 角色当前状态\n{character_states}\n\n"
        if unresolved_threads:
            user_content += f"## 待处理线索\n{unresolved_threads}\n\n"

        user_content += """
请以JSON格式输出：
```json
{
  "chapter_num": N,
  "title": "章节标题",
  "summary": "本章概要（100-200字）",
  "pov": "主视角角色",
  "emotional_arc": "情绪弧线描述",
  "key_events": ["事件1", "事件2"],
  "scene_sketches": [
    {"location": "地点", "characters": ["角色"], "action": "场景行动描述"}
  ],
  "foreshadowing": ["要埋的伏笔"],
  "payoffs": ["要回收的伏笔"]
}
```"""
        resp = await self.invoke(
            user_content, constraints=constraints,
            temperature=0.6, max_tokens=3000, parse_json=True,
        )
        return resp.raw.get("parsed") or {"chapter_num": chapter_num, "summary": resp.content}
