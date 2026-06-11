"""DecisionSampler — seeded behavior directives (Actor·机制1/2, 角色卡·机制5)."""
from __future__ import annotations

import re

from knowledge.decision_engine import (
    BehaviorDirective, DecisionSampler, _as_probability,
)


class TestSeededReproducibility:
    def test_same_seed_same_directive(self) -> None:
        a = DecisionSampler(project_id="p", chapter_num=3, scene_index=0,
                            base_seed="user-seed")
        b = DecisionSampler(project_id="p", chapter_num=3, scene_index=0,
                            base_seed="user-seed")
        da = a.sample("张远", {"impulse_probability": 0.5})
        db = b.sample("张远", {"impulse_probability": 0.5})
        assert da == db
        assert da.to_text() == db.to_text()

    def test_different_seed_can_differ(self) -> None:
        draws = set()
        for seed in range(20):
            s = DecisionSampler(project_id="p", chapter_num=1,
                                scene_index=0, base_seed=seed)
            d = s.sample("张远", {"impulse_probability": 0.5,
                                  "loss_aversion": 0.5,
                                  "social_frequency": 0.5})
            draws.add((d.impulsive, d.conservative_vs_loss,
                       d.socially_active))
        assert len(draws) > 1

    def test_per_character_draws_independent(self) -> None:
        """Changing one character's presence must not shift another's draw."""
        s = DecisionSampler(project_id="p", chapter_num=2, scene_index=1,
                            base_seed=7)
        solo = s.sample("李清漪")
        pair = s.sample_scene(["张远", "李清漪"])
        assert pair["李清漪"] == solo


class TestProbabilities:
    def test_extreme_probabilities(self) -> None:
        s = DecisionSampler(base_seed=1)
        d = s.sample("甲", {"impulse_probability": 1.0})
        assert d.impulsive
        d2 = s.sample("乙", {"impulse_probability": 0.0,
                             "loss_aversion": 1.0,
                             "risk_aversion": 1.0,
                             "social_frequency": 0.0})
        assert not d2.impulsive
        assert d2.conservative_vs_loss
        assert d2.conservative_vs_gain
        assert not d2.socially_active

    def test_legacy_lambda_normalized(self) -> None:
        # prospect-theory λ=2.0 → 2/3 probability
        assert abs(_as_probability(2.0, 0.5) - 2.0 / 3.0) < 1e-9
        assert _as_probability(0.3, 0.5) == 0.3
        assert _as_probability(None, 0.4) == 0.4
        assert _as_probability(-1, 0.4) == 0.0


class TestDirectiveText:
    def test_text_contains_name_and_no_emoji(self) -> None:
        d = BehaviorDirective(
            character_name="张远", impulsive=False,
            conservative_vs_loss=True, conservative_vs_gain=False,
            socially_active=True, seed=42,
        )
        text = d.to_text()
        assert text.startswith("张远：")
        assert "保守" in text
        # 数据标准·机制2: no emoji anywhere in generated artifacts
        assert not re.search(r"[\U0001F000-\U0001FAFF☀-➿]", text)

    def test_impulsive_overrides_conservative(self) -> None:
        s = DecisionSampler(base_seed=3)
        d = s.sample("丙", {"impulse_probability": 1.0,
                            "loss_aversion": 1.0})
        assert d.impulsive
        assert not d.conservative_vs_loss
        assert "冲动" in d.to_text()
