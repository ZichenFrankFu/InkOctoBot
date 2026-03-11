"""
Quality Score skill — rule-based composite quality scoring.

Aggregates multiple evaluation dimension scores into a single
weighted quality metric. No LLM calls required.
"""
from __future__ import annotations

import logging
from typing import Any

from agents.base_skill import BaseSkill, SkillMeta

logger = logging.getLogger("inkoctobot.skills.quality_score")

# Dimension -> weight mapping
_WEIGHTS: dict[str, float] = {
    "constraint_satisfaction": 0.25,
    "consistency": 0.20,
    "knowledge_isolation": 0.15,
    "repetition": 0.10,
    "style_quality": 0.15,
    "pacing": 0.10,
    "slop_free": 0.05,
}

_DEFAULT_PASS_THRESHOLD = 60.0


class Skill(BaseSkill):
    """Rule-based quality scorer — overrides execute() to skip LLM."""

    def __init__(self, pass_threshold: float = _DEFAULT_PASS_THRESHOLD) -> None:
        self.pass_threshold = pass_threshold

    def meta(self) -> SkillMeta:
        return SkillMeta(
            name="quality_score",
            display_name="质量评分",
            description=(
                "基于规则的复合质量评分聚合器，将多维度评分加权汇总为最终分数，"
                "无需LLM调用。"
            ),
            version="1.0.0",
            model_role="none",
            tags=["evaluation", "quality", "scoring", "rule-based"],
            permissions=[],
            input_schema={
                "type": "object",
                "required": ["dimension_scores"],
                "properties": {
                    "dimension_scores": {
                        "type": "object",
                        "additionalProperties": {"type": "number"},
                    },
                },
            },
            output_schema={
                "type": "object",
                "required": ["overall_score", "dimension_scores", "passed"],
                "properties": {
                    "overall_score": {"type": "number"},
                    "dimension_scores": {"type": "object"},
                    "passed": {"type": "boolean"},
                },
            },
        )

    async def build_prompt(self, inputs: dict[str, Any]) -> str:
        # Not used — execute() is overridden.
        return ""

    async def parse_output(self, raw: str) -> dict[str, Any]:
        # Not used — execute() is overridden.
        return {}

    async def execute(
        self,
        inputs: dict[str, Any],
        model_router: Any = None,
    ) -> dict[str, Any]:
        """Run rule-based quality scoring directly (no LLM)."""
        dimension_scores: dict[str, float] = inputs.get("dimension_scores", {})

        weighted_sum = 0.0
        total_weight = 0.0
        for dim, weight in _WEIGHTS.items():
            if dim in dimension_scores:
                weighted_sum += dimension_scores[dim] * weight
                total_weight += weight

        overall = weighted_sum / total_weight if total_weight > 0 else 0.0

        return {
            "overall_score": round(overall, 1),
            "dimension_scores": {k: round(v, 1) for k, v in dimension_scores.items()},
            "passed": overall >= self.pass_threshold,
        }
