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
            lines.append(f"冲动倾向: 强烈倾向于「{scored[0][1].get('action', '')}」")
        else:
            for i, (u, opt) in enumerate(scored[:3]):
                indicator = "(首选)" if i == 0 else "      "
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


# ──────────────────────────────────────────────────────────────
# 决策采样器 (spec Actor·机制1/机制2 + 角色卡·机制5)
#
# Code-only — never calls an LLM. Per scene, per character, it does
# seeded Bernoulli draws over the character's quantitative parameters
# (损失厌恶 / 收益风险厌恶 / 冲动概率 / 社交频率) and emits ONE
# natural-language behavior directive, which the ensemble Actor call
# receives as a constraint. The Actor LLM only dramatizes the
# directive; it does not decide the tendencies itself.
# ──────────────────────────────────────────────────────────────

import hashlib


@dataclass
class BehaviorDirective:
    """One sampled directive for one character in one scene."""
    character_name: str
    impulsive: bool
    conservative_vs_loss: bool   # 面对损失风险是否保守
    conservative_vs_gain: bool   # 面对收益风险是否保守
    socially_active: bool        # 是否主动与陌生角色建立联系
    seed: int

    def to_text(self) -> str:
        if self.impulsive:
            risk_part = "本场行为冲动，倾向激进行动（主动出击、不计后果），无视常规风险考量"
        else:
            loss_part = (
                "面对有损失风险的处境倾向保守（回避、等待）"
                if self.conservative_vs_loss
                else "面对有损失风险的处境敢于行动"
            )
            gain_part = (
                "面对需冒险才能获益的机会倾向放弃或观望"
                if self.conservative_vs_gain
                else "面对需冒险才能获益的机会愿意一搏"
            )
            risk_part = f"{loss_part}；{gain_part}"
        social_part = (
            "遇到陌生角色会主动建立联系"
            if self.socially_active
            else "不主动与陌生角色攀谈"
        )
        return f"{self.character_name}：{risk_part}；{social_part}"


def _stable_seed(*parts: Any) -> int:
    """Deterministic 64-bit seed from arbitrary parts (角色卡·机制5).

    Built on sha256 — Python's builtin ``hash()`` is salted per process
    and would break cross-run reproducibility.
    """
    raw = "::".join(str(p) for p in parts)
    return int.from_bytes(
        hashlib.sha256(raw.encode("utf-8")).digest()[:8], "big",
    )


def _as_probability(value: Any, default: float) -> float:
    """Normalize a character-card decision parameter to [0, 1].

    Values already in [0, 1] are taken as Bernoulli probabilities (the
    spec's contract). Legacy prospect-theory loss-aversion lambdas
    (λ ≥ 1, e.g. 2.0) are mapped via λ/(1+λ) so a stronger aversion
    still means a higher chance of conservative behavior.
    """
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    if v < 0:
        return 0.0
    if v <= 1.0:
        return v
    return v / (1.0 + v)


class DecisionSampler:
    """Seeded sampler producing per-character behavior directives.

    ``base_seed`` lets users replay an identical sampling outcome
    (角色卡·机制5); per-character draws are further keyed by
    (project_id, chapter_num, scene_index, character_name) so changing
    one character never shifts another character's draw.
    """

    def __init__(
        self, *, project_id: str = "", chapter_num: int = 0,
        scene_index: int = 0, base_seed: int | str | None = None,
    ):
        self.project_id = project_id
        self.chapter_num = chapter_num
        self.scene_index = scene_index
        self.base_seed = base_seed if base_seed is not None else 0

    def sample(
        self, character_name: str, params: dict[str, Any] | None = None,
    ) -> BehaviorDirective:
        """Draw one directive for ``character_name``.

        ``params`` keys (all optional, probabilities in [0, 1]):
        ``loss_aversion`` / ``risk_aversion`` (alias
        ``risk_aversion_gain``) / ``impulse_probability`` /
        ``social_frequency`` (alias ``talk_frequency``).
        """
        p = params or {}
        seed = _stable_seed(
            self.base_seed, self.project_id, self.chapter_num,
            self.scene_index, character_name,
        )
        rng = random.Random(seed)

        p_impulse = _as_probability(p.get("impulse_probability"), 0.1)
        p_loss = _as_probability(p.get("loss_aversion"), 0.6)
        p_gain = _as_probability(
            p.get("risk_aversion", p.get("risk_aversion_gain")), 0.5,
        )
        p_social = _as_probability(
            p.get("social_frequency", p.get("talk_frequency")), 0.5,
        )

        # 冲动采样优先：命中即覆盖保守倾向（spec 量化决策参数定义）。
        impulsive = rng.random() < p_impulse
        return BehaviorDirective(
            character_name=character_name,
            impulsive=impulsive,
            conservative_vs_loss=(not impulsive) and rng.random() < p_loss,
            conservative_vs_gain=(not impulsive) and rng.random() < p_gain,
            socially_active=rng.random() < p_social,
            seed=seed,
        )

    def sample_scene(
        self, characters: list[str],
        params_by_character: dict[str, dict[str, Any]] | None = None,
    ) -> dict[str, BehaviorDirective]:
        """Sample directives for every character in the scene."""
        params_by_character = params_by_character or {}
        return {
            name: self.sample(name, params_by_character.get(name))
            for name in characters
        }
