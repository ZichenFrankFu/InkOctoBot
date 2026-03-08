"""
Story Architect — helps users refine world book, character cards, and outline.

README §2.2.2: Interactive disambiguation system that identifies vague or
incomplete settings and generates clarification questions.
"""
from __future__ import annotations

import logging
from typing import Any

from agents.base_agent import BaseAgent

logger = logging.getLogger("inkoctobot.agents.planner.story_architect")


class StoryArchitect(BaseAgent):
    agent_name = "story_architect"

    async def analyze_settings(
        self,
        world_book: str,
        character_cards: str,
        outline: str,
        *,
        reference_context: str = "",
    ) -> dict[str, Any]:
        """Analyze user settings and generate disambiguation questions."""
        user_content = (
            "请分析以下创作设定，识别模糊、不完整或可能矛盾的部分，"
            "并生成针对性的细化问题。\n\n"
            f"## 世界书\n{world_book}\n\n"
            f"## 角色设定\n{character_cards}\n\n"
            f"## 大纲\n{outline}"
        )
        context = ""
        if reference_context:
            context = f"[参考作品信息]\n{reference_context}"

        resp = await self.invoke(
            user_content, context=context, temperature=0.6,
            max_tokens=4000, parse_json=True,
        )
        parsed = resp.raw.get("parsed")
        if parsed:
            return parsed
        return {"analysis": resp.content, "questions": []}

    async def refine_world_book(self, world_book: str, user_answers: dict[str, str]) -> str:
        """Refine world book based on user's answers to disambiguation questions."""
        answers_text = "\n".join(f"Q: {q}\nA: {a}" for q, a in user_answers.items())
        user_content = (
            f"根据以下问答，细化并完善世界书设定。\n\n"
            f"## 原始世界书\n{world_book}\n\n"
            f"## 用户回答\n{answers_text}\n\n"
            f"请输出完善后的世界书（保留原有内容，补充细节）。"
        )
        resp = await self.invoke(user_content, temperature=0.5, max_tokens=6000)
        return resp.content

    async def refine_character(self, character_card: str, user_answers: dict[str, str]) -> str:
        """Refine a character card based on user answers."""
        answers_text = "\n".join(f"Q: {q}\nA: {a}" for q, a in user_answers.items())
        user_content = (
            f"根据以下问答，细化角色设定。\n\n"
            f"## 原始角色卡\n{character_card}\n\n"
            f"## 用户回答\n{answers_text}\n\n"
            f"请输出完善后的角色卡。"
        )
        resp = await self.invoke(user_content, temperature=0.5, max_tokens=4000)
        return resp.content

    async def check_consistency(
        self, world_book: str, character_cards: str, outline: str,
    ) -> dict[str, Any]:
        """Check for consistency issues across all settings."""
        user_content = (
            "请检查以下设定之间的一致性问题，包括:\n"
            "1. 世界观规则之间的矛盾\n"
            "2. 角色设定与世界观的冲突\n"
            "3. 大纲情节与已有设定的不一致\n\n"
            f"## 世界书\n{world_book}\n\n"
            f"## 角色设定\n{character_cards}\n\n"
            f"## 大纲\n{outline}\n\n"
            "请以JSON格式输出检查结果。"
        )
        resp = await self.invoke(
            user_content, temperature=0.3, max_tokens=3000, parse_json=True,
        )
        return resp.raw.get("parsed") or {"issues": [], "analysis": resp.content}
