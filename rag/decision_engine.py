"""
Quantitative Decision Engine — Character Card Layer B.

README §3.1.1: Implements utility functions, prospect theory parameters,
stochastic behavior distributions, and Bayesian trust tracking for
character decision-making.
"""
from __future__ import annotations

import logging
import math
import random
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("inkoctobot.rag.decision_engine")


@dataclass
class ValueWeights:
    """Utility function value dimension weights."""
    survival: float = 0.3
    power: float = 0.2
    love: float = 0.15
    loyalty: float = 0.15
    justice: float = 0.1
    freedom: float = 0.1
    time_discount: float = 0.95  # how much future value is discounted


@dataclass
class ProspectParams:
    """Prospect theory parameters for risk/loss evaluation."""
    loss_aversion: float = 2.0      # lambda: loss feels 2x worse than equivalent gain
    risk_aversion_gain: float = 0.88
    risk_aversion_loss: float = 0.88
    reference_point: float = 0.0


@dataclass
class StochasticParams:
    """Random behavior distributions."""
    talk_frequency: float = 1.0       # Poisson lambda for initiating conversation
    impulse_probability: float = 0.1  # Bernoulli p for impulsive actions
    emotion_volatility: float = 0.5   # how much emotions fluctuate


@dataclass
class BayesianTrust:
    """Beta distribution trust tracker."""
    alpha: float = 5.0   # positive observations
    beta: float = 5.0    # negative observations

    @property
    def trust_level(self) -> float:
        return self.alpha / (self.alpha + self.beta)

    def observe_positive(self, weight: float = 1.0) -> None:
        self.alpha += weight

    def observe_negative(self, weight: float = 1.0) -> None:
        self.beta += weight

    def to_dict(self) -> dict[str, float]:
        return {"alpha": self.alpha, "beta": self.beta, "trust": round(self.trust_level, 3)}


@dataclass
class DecisionModel:
    """Complete decision model for a character."""
    character_name: str
    values: ValueWeights = field(default_factory=ValueWeights)
    prospect: ProspectParams = field(default_factory=ProspectParams)
    stochastic: StochasticParams = field(default_factory=StochasticParams)
    trust_map: dict[str, BayesianTrust] = field(default_factory=dict)

    def compute_action_utility(
        self,
        action: str,
        outcomes: dict[str, float],
        situation_modifiers: dict[str, float] | None = None,
    ) -> float:
        """
        Compute expected utility for an action.

        outcomes: {dimension_name: expected_change} e.g. {"survival": 0.5, "loyalty": -0.3}
        situation_modifiers: optional context-dependent adjustments
        """
        weights = self.values
        mods = situation_modifiers or {}

        base_utility = 0.0
        for dim, change in outcomes.items():
            w = getattr(weights, dim, 0.1) + mods.get(dim, 0.0)
            # Apply prospect theory
            if change >= 0:
                value = (change ** self.prospect.risk_aversion_gain) * w
            else:
                value = -self.prospect.loss_aversion * ((-change) ** self.prospect.risk_aversion_loss) * w
            base_utility += value

        # Stochastic perturbation
        noise = random.gauss(0, self.stochastic.emotion_volatility * 0.1)
        return base_utility + noise

    def should_act_impulsively(self) -> bool:
        """Bernoulli check for impulsive action."""
        return random.random() < self.stochastic.impulse_probability

    def conversation_likelihood(self) -> int:
        """Poisson sample for conversation initiation."""
        return random.randint(0, max(1, int(self.stochastic.talk_frequency * 2)))

    def get_trust(self, target: str) -> float:
        if target not in self.trust_map:
            self.trust_map[target] = BayesianTrust()
        return self.trust_map[target].trust_level

    def update_trust(self, target: str, positive: bool, weight: float = 1.0) -> float:
        if target not in self.trust_map:
            self.trust_map[target] = BayesianTrust()
        if positive:
            self.trust_map[target].observe_positive(weight)
        else:
            self.trust_map[target].observe_negative(weight)
        return self.trust_map[target].trust_level

    def generate_guidance(self, situation: str, options: list[dict[str, Any]]) -> str:
        """
        Generate natural language guidance from quantitative decision.

        This output is injected into the Actor Agent's prompt to guide behavior.
        """
        scored = []
        for opt in options:
            utility = self.compute_action_utility(
                opt.get("action", ""),
                opt.get("outcomes", {}),
            )
            scored.append((utility, opt))
        scored.sort(key=lambda x: x[0], reverse=True)

        lines = [f"[决策引擎 — {self.character_name}]"]
        lines.append(f"情境: {situation}")

        if self.should_act_impulsively() and scored:
            lines.append(f"⚡ 冲动倾向: 强烈倾向于「{scored[0][1].get('action', '')}」")
        else:
            for i, (u, opt) in enumerate(scored[:3]):
                indicator = "→" if i == 0 else " "
                lines.append(f"{indicator} {opt.get('action', '')}: 效用={u:.2f}")

        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "character_name": self.character_name,
            "values": {k: v for k, v in self.values.__dict__.items()},
            "prospect": {k: v for k, v in self.prospect.__dict__.items()},
            "stochastic": {k: v for k, v in self.stochastic.__dict__.items()},
            "trust_map": {k: v.to_dict() for k, v in self.trust_map.items()},
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DecisionModel:
        model = cls(character_name=data.get("character_name", ""))
        if "values" in data:
            for k, v in data["values"].items():
                setattr(model.values, k, v)
        if "prospect" in data:
            for k, v in data["prospect"].items():
                setattr(model.prospect, k, v)
        if "stochastic" in data:
            for k, v in data["stochastic"].items():
                setattr(model.stochastic, k, v)
        if "trust_map" in data:
            for name, td in data["trust_map"].items():
                model.trust_map[name] = BayesianTrust(alpha=td["alpha"], beta=td["beta"])
        return model
