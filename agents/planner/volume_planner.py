"""
Volume Planner — divides the novel's overall outline into volumes.

Helps structure long novels into manageable volumes with clear
arcs, turning points, and pacing.
"""
from __future__ import annotations

import logging
from typing import Any

from agents.base_agent import BaseAgent

logger = logging.getLogger("inkoctobot.agents.planner.volume_planner")


class VolumePlanner(BaseAgent):
    agent_name = "volume_planner"

    async def plan_volumes(
        self,
        overall_outline: str,
        *,
        target_chapters_per_volume: int = 30,
        world_book_summary: str = "",
        character_list: str = "",
    ) -> dict[str, Any]:
        """Divide overall outline into volume-level plans."""
        user_content = (
            f"请将以下小说总大纲分为分卷计划，每卷约{target_chapters_per_volume}章。\n\n"
            f"## 总大纲\n{overall_outline}\n\n"
        )
        if world_book_summary:
            user_content += f"## 世界观概要\n{world_book_summary}\n\n"
        if character_list:
            user_content += f"## 主要角色\n{character_list}\n\n"

        user_content += """
请以JSON格式输出分卷计划：
```json
{
  "total_volumes": N,
  "volumes": [
    {
      "volume_num": 1,
      "title": "卷名",
      "chapter_range": [1, 30],
      "arc_summary": "本卷主线概要",
      "turning_point": "关键转折点",
      "new_characters": ["新角色"],
      "pacing": "节奏描述（快/中/慢）"
    }
  ]
}
```"""
        resp = await self.invoke(
            user_content, temperature=0.5, max_tokens=4000, parse_json=True,
        )
        return resp.raw.get("parsed") or {"volumes": [], "analysis": resp.content}
