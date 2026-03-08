"""
Cross-chapter continuity checker — foreshadowing audit, character drift detection.

Operates across multiple chapters to detect arc-level issues.
"""
from __future__ import annotations

import logging
from typing import Any

from agents.base_agent import BaseAgent

logger = logging.getLogger("inkoctobot.agents.evaluation.cross_chapter_checker")


class CrossChapterChecker(BaseAgent):
    agent_name = "cross_chapter_checker"

    async def audit_foreshadowing(
        self,
        unresolved: list[dict[str, Any]],
        current_chapter: int,
        recent_text: str = "",
    ) -> dict[str, Any]:
        """Check if any foreshadowing is overdue for resolution."""
        if not unresolved:
            return {"overdue": [], "warnings": []}

        items = "\n".join(
            f"- 第{f['chapter_num']}章: {f['description']} (距今{current_chapter - f['chapter_num']}章)"
            for f in unresolved
        )
        user_content = (
            f"当前为第{current_chapter}章。以下伏笔尚未回收:\n{items}\n\n"
            "请判断哪些伏笔已超期需要提醒（一般超过10章未回收视为超期），"
            "以及最近章节文本中是否有暗示可能回收的线索。\n"
        )
        if recent_text:
            user_content += f"\n近期文本片段:\n{recent_text[:2000]}\n"

        user_content += '请以JSON输出: {"overdue": [...], "potential_payoffs": [...], "warnings": [...]}'

        resp = await self.invoke(
            user_content, temperature=0.3, max_tokens=2000, parse_json=True,
        )
        return resp.raw.get("parsed") or {"overdue": [], "warnings": []}

    async def detect_character_drift(
        self,
        character_name: str,
        character_card: str,
        recent_behaviors: str,
    ) -> dict[str, Any]:
        """Detect if a character's behavior is drifting from their established personality."""
        user_content = (
            f"请分析角色「{character_name}」的近期行为是否偏离了角色设定。\n\n"
            f"## 角色设定\n{character_card}\n\n"
            f"## 近期行为\n{recent_behaviors}\n\n"
            '请以JSON输出: {"drifted": true/false, "drift_description": "...", '
            '"severity": "high|medium|low", "suggestions": [...]}'
        )
        resp = await self.invoke(
            user_content, temperature=0.3, max_tokens=1500, parse_json=True,
        )
        return resp.raw.get("parsed") or {"drifted": False}
