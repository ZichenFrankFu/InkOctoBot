"""
Quality Scorer — composite quality score for generated text.

Aggregates scores from multiple evaluation dimensions into
a single quality metric.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("inkoctobot.agents.evaluation.quality_scorer")


@dataclass
class QualityReport:
    """Composite quality report."""
    overall_score: float = 0.0
    dimension_scores: dict[str, float] = field(default_factory=dict)
    passed: bool = True
    pass_threshold: float = 60.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "overall_score": round(self.overall_score, 1),
            "dimension_scores": {k: round(v, 1) for k, v in self.dimension_scores.items()},
            "passed": self.passed,
        }


class QualityScorer:
    """Aggregates evaluation dimensions into a composite score."""

    # dimension -> weight
    WEIGHTS: dict[str, float] = {
        "constraint_satisfaction": 0.25,
        "consistency": 0.20,
        "knowledge_isolation": 0.15,
        "repetition": 0.10,
        "style_quality": 0.15,
        "pacing": 0.10,
        "slop_free": 0.05,
    }

    def __init__(self, pass_threshold: float = 60.0):
        self.pass_threshold = pass_threshold

    def score(self, dimension_scores: dict[str, float]) -> QualityReport:
        """Compute composite score from individual dimensions."""
        weighted_sum = 0.0
        total_weight = 0.0
        for dim, weight in self.WEIGHTS.items():
            if dim in dimension_scores:
                weighted_sum += dimension_scores[dim] * weight
                total_weight += weight

        overall = weighted_sum / total_weight if total_weight > 0 else 0.0

        return QualityReport(
            overall_score=overall,
            dimension_scores=dimension_scores,
            passed=overall >= self.pass_threshold,
            pass_threshold=self.pass_threshold,
        )
