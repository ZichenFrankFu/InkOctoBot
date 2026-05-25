"""
Commercial API cost estimator.

Pre-estimates token usage and cost before running a pipeline step,
prompts user for confirmation if cost exceeds threshold.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("inkoctobot.agents.cost_estimator")


@dataclass
class CostEstimate:
    """Estimated cost for a single pipeline step."""
    agent_role: str
    estimated_input_tokens: int
    estimated_output_tokens: int
    estimated_cost_usd: float
    model_name: str = ""


@dataclass
class SessionCostTracker:
    """Tracks cumulative costs across a writing session."""
    estimates: list[CostEstimate] = field(default_factory=list)
    actuals: list[dict[str, Any]] = field(default_factory=list)
    budget_usd: float = 5.0

    @property
    def total_estimated(self) -> float:
        return sum(e.estimated_cost_usd for e in self.estimates)

    @property
    def total_actual(self) -> float:
        return sum(a.get("cost_usd", 0.0) for a in self.actuals)

    @property
    def remaining_budget(self) -> float:
        return max(0.0, self.budget_usd - self.total_actual)

    def add_estimate(self, est: CostEstimate) -> None:
        self.estimates.append(est)

    def record_actual(self, agent_role: str, input_tokens: int,
                      output_tokens: int, cost_usd: float) -> None:
        self.actuals.append({
            "agent_role": agent_role,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cost_usd": cost_usd,
        })

    def summary(self) -> dict[str, Any]:
        return {
            "total_estimated_usd": round(self.total_estimated, 4),
            "total_actual_usd": round(self.total_actual, 4),
            "budget_usd": self.budget_usd,
            "remaining_usd": round(self.remaining_budget, 4),
            "calls": len(self.actuals),
        }


class CostEstimator:
    """Estimate costs before executing pipeline steps."""

    # rough token estimates per agent role (input, output)
    _ESTIMATES: dict[str, tuple[int, int]] = {
        "scene_director":  (3000, 2000),
        "actor_agent":     (2000, 1500),
        "narrator_agent":  (2000, 1000),
        "editor_writer":   (5000, 4000),
        "evaluator":       (4000, 1500),
        "story_architect": (3000, 2000),
        "calibration":     (2000, 1500),
        "marketing_agent": (1000,  800),
        "edit_analyzer":   (3000, 1000),
    }

    def __init__(self, router: Any):
        self.router = router

    def estimate_step(self, agent_role: str, context_tokens: int = 0) -> CostEstimate:
        base_in, base_out = self._ESTIMATES.get(agent_role, (2000, 1500))
        total_in = base_in + context_tokens
        cost = self.router.estimate_cost(agent_role, total_in, base_out)
        return CostEstimate(
            agent_role=agent_role,
            estimated_input_tokens=total_in,
            estimated_output_tokens=base_out,
            estimated_cost_usd=cost,
        )

    def estimate_chapter_pipeline(self, context_tokens: int = 0) -> list[CostEstimate]:
        """Estimate full chapter generation cost."""
        roles = ["scene_director", "actor_agent", "narrator_agent",
                 "editor_writer", "evaluator"]
        return [self.estimate_step(r, context_tokens) for r in roles]
