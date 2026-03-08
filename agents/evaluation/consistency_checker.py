"""
Consistency Checker — validates character behavior and world rules.

Checks that generated text is consistent with established character
settings, world book rules, and prior events.
"""
from __future__ import annotations

import logging
from typing import Any

from agents.base_agent import BaseAgent

logger = logging.getLogger("inkoctobot.agents.evaluation.consistency_checker")


class ConsistencyChecker(BaseAgent):
    agent_name = "consistency_checker"

    async def check(
        self,
        text: str,
        *,
        character_cards: str = "",
        world_rules: str = "",
        previous_events: str = "",
    ) -> dict[str, Any]:
        """Check text against established settings for consistency issues."""
        user_content = (
            "请检查以下文本是否与已有设定一致。\n\n"
            f"## 文本\n{text}\n\n"
        )
        if character_cards:
            user_content += f"## 角色设定\n{character_cards}\n\n"
        if world_rules:
            user_content += f"## 世界观规则\n{world_rules}\n\n"
        if previous_events:
            user_content += f"## 前情事件\n{previous_events}\n\n"

        user_content += (
            "请以JSON输出：\n```json\n"
            '{"consistent": true/false, "issues": ['
            '{"type": "character|world|continuity", "description": "...", "severity": "high|medium|low"}'
            "]}\n```"
        )
        resp = await self.invoke(
            user_content, temperature=0.3, max_tokens=2000, parse_json=True,
        )
        return resp.raw.get("parsed") or {"consistent": True, "issues": []}
