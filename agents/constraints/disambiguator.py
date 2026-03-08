"""
Prompt Disambiguator — generates clarification questions for vague constraints.

README §2.5 & §4.3: When a constraint or setting is ambiguous, generate
multiple candidate interpretations for user selection.
"""
from __future__ import annotations

import logging
from typing import Any

from agents.base_agent import BaseAgent

logger = logging.getLogger("inkoctobot.agents.constraints.disambiguator")


class Disambiguator(BaseAgent):
    agent_name = "disambiguator"

    async def disambiguate(
        self,
        ambiguous_text: str,
        context: str = "",
    ) -> dict[str, Any]:
        """Generate clarification options for an ambiguous input."""
        user_content = (
            "以下文本存在模糊性或多种可能解读，请生成细化选择题。\n\n"
            f"## 原始文本\n{ambiguous_text}\n\n"
            "请以JSON格式输出：\n```json\n"
            "{\n"
            '  "ambiguities": [\n'
            '    {\n'
            '      "aspect": "模糊的方面",\n'
            '      "question": "面向用户的问题",\n'
            '      "options": [\n'
            '        {"label": "选项A", "description": "详细解释"},\n'
            '        {"label": "选项B", "description": "详细解释"}\n'
            '      ]\n'
            '    }\n'
            '  ]\n'
            '}\n```'
        )
        resp = await self.invoke(
            user_content, context=context,
            temperature=0.5, max_tokens=2000, parse_json=True,
        )
        return resp.raw.get("parsed") or {"ambiguities": []}

    async def resolve(
        self,
        original_text: str,
        user_choices: dict[str, str],
    ) -> str:
        """Rewrite the original text incorporating user's disambiguation choices."""
        choices_text = "\n".join(f"- {q}: {a}" for q, a in user_choices.items())
        user_content = (
            f"请根据用户的选择，将以下模糊文本改写为明确的表述。\n\n"
            f"## 原始文本\n{original_text}\n\n"
            f"## 用户选择\n{choices_text}\n\n"
            f"请输出改写后的明确文本。"
        )
        resp = await self.invoke(user_content, temperature=0.3, max_tokens=1500)
        return resp.content
