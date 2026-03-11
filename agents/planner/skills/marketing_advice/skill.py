"""
marketing_advice skill — data-driven market trend analysis.

Wraps MarketingAgent into a BaseSkill-compatible interface.
Non-LLM skill: build_prompt formats query params, parse_output passes through.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from agents.base_skill import BaseSkill, SkillMeta
from agents.planner.marketing_agent import MarketingAgent

logger = logging.getLogger("inkoctobot.skills.marketing_advice")


class Skill(BaseSkill):
    """Data-driven marketing advice skill (no LLM required)."""

    def meta(self) -> SkillMeta:
        return SkillMeta(
            name="marketing_advice",
            display_name="市场营销建议",
            description="数据驱动的市场趋势分析，提供选题、类型热度、竞争度建议",
            version="1.0.0",
            model_role="default",
            tags=["planner", "marketing", "data-driven"],
            permissions=["read_reference_db"],
            input_schema={
                "type": "object",
                "properties": {
                    "genre": {"type": "string", "description": "目标类型/分类"},
                    "db_path": {"type": "string", "description": "参考数据库路径"},
                    "analysis_type": {
                        "type": "string",
                        "enum": ["full", "heat", "trend", "competition", "tags"],
                        "default": "full",
                        "description": "分析类型",
                    },
                    "lookback_days": {
                        "type": "integer",
                        "default": 90,
                        "description": "趋势分析回溯天数",
                    },
                },
                "required": ["genre", "db_path"],
            },
            output_schema={
                "type": "object",
                "properties": {
                    "genre": {"type": "string"},
                    "summary": {"type": "string"},
                    "advices": {"type": "array"},
                    "raw": {"type": "object"},
                },
            },
            # Non-LLM skill: these are unused but kept for interface consistency
            max_tokens=1,
            temperature=0.0,
        )

    async def build_prompt(self, inputs: dict[str, Any]) -> str:
        """Format query parameters as a JSON string.

        Since this is a data-driven (non-LLM) skill, the prompt is simply
        a serialised representation of the query for logging/tracing purposes.
        """
        return json.dumps(inputs, ensure_ascii=False)

    async def parse_output(self, raw: str) -> dict[str, Any]:
        """Pass through — the result is already structured data."""
        if isinstance(raw, dict):
            return raw
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return {"raw_text": raw}

    # ── Override execute: skip LLM, run DB queries directly ──────────

    async def execute(
        self,
        inputs: dict[str, Any],
        model_router: Any = None,
    ) -> dict[str, Any]:
        """Execute the marketing analysis via direct DB queries (no LLM)."""
        genre: str = inputs["genre"]
        db_path: str = inputs["db_path"]
        analysis_type: str = inputs.get("analysis_type", "full")

        agent = MarketingAgent(db_path)

        if analysis_type == "full":
            return agent.advise_genre(genre)
        elif analysis_type == "heat":
            return agent.genre_heat(genre)
        elif analysis_type == "trend":
            lookback = inputs.get("lookback_days", 90)
            return agent.genre_trend(genre, lookback_days=lookback)
        elif analysis_type == "competition":
            return agent.genre_competition(genre)
        elif analysis_type == "tags":
            return {"genre": genre, "tags": agent.popular_tags(genre)}
        else:
            return agent.advise_genre(genre)
