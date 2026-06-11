"""单章成本预估 + 降级阶梯 (spec LLM调用·机制1)."""
from __future__ import annotations

import pytest

from ui.backend.app.services.generation_cost import (
    estimate_chapter, estimate_with_degradation,
)


class _FakeRouter:
    """USD = tokens / 1000 * 0.01 — deterministic for assertions."""
    def estimate_cost(self, role, input_tokens, output_tokens):
        return (input_tokens + output_tokens) / 1000 * 0.01


class TestEstimate:
    def test_single_mode_is_three_calls(self) -> None:
        est = estimate_chapter("single", router=_FakeRouter())
        # Writer + 状态抽取 + 评估 = 每章 3 次 (spec 调用小结)
        assert est["llm_calls"] == 3
        assert {b["call"] for b in est["breakdown"]} == {
            "writer", "state_extract", "evaluator_llm",
        }

    def test_director_mode_scales_with_scenes(self) -> None:
        e3 = estimate_chapter("director", num_scenes=3, router=_FakeRouter())
        e6 = estimate_chapter("director", num_scenes=6, router=_FakeRouter())
        # Scene Director(1) + (Actor+Narrator)*scenes + Writer + 抽取 + 评估
        assert e3["llm_calls"] == 1 + 3 * 2 + 3
        assert e6["llm_calls"] == 1 + 6 * 2 + 3
        assert e6["estimated_usd"] > e3["estimated_usd"]

    def test_context_tokens_added_to_every_call(self) -> None:
        base = estimate_chapter("single", router=_FakeRouter())
        padded = estimate_chapter(
            "single", context_tokens=1000, router=_FakeRouter(),
        )
        assert padded["input_tokens"] == base["input_tokens"] + 3 * 1000

    def test_skip_eval_drops_one_call(self) -> None:
        est = estimate_chapter(
            "single", skip_llm_eval=True, router=_FakeRouter(),
        )
        assert est["llm_calls"] == 2
        assert all(b["call"] != "evaluator_llm" for b in est["breakdown"])


class TestDegradation:
    def test_no_cap_returns_requested(self) -> None:
        out = estimate_with_degradation(
            "director", 4, cap_usd=0, router=_FakeRouter(),
        )
        assert out["effective"]["degraded"] is False
        assert out["effective"]["mode"] == "director"

    def test_under_cap_no_degradation(self) -> None:
        out = estimate_with_degradation(
            "director", 3, cap_usd=100.0, router=_FakeRouter(),
        )
        assert out["effective"]["degraded"] is False

    def test_step1_director_to_single(self) -> None:
        director = estimate_chapter("director", 4, router=_FakeRouter())
        single = estimate_chapter("single", router=_FakeRouter())
        cap = (director["estimated_usd"] + single["estimated_usd"]) / 2
        out = estimate_with_degradation(
            "director", 4, cap_usd=cap, router=_FakeRouter(),
        )
        eff = out["effective"]
        assert eff["degraded"] is True
        assert eff["mode"] == "single"
        assert eff["degradation_steps"] == ["director_to_single"]
        assert eff["over_cap"] is False

    def test_step2_skip_llm_eval(self) -> None:
        single = estimate_chapter("single", router=_FakeRouter())
        no_eval = estimate_chapter(
            "single", skip_llm_eval=True, router=_FakeRouter(),
        )
        cap = (single["estimated_usd"] + no_eval["estimated_usd"]) / 2
        out = estimate_with_degradation(
            "director", 4, cap_usd=cap, router=_FakeRouter(),
        )
        eff = out["effective"]
        assert eff["degradation_steps"] == [
            "director_to_single", "skip_llm_eval",
        ]
        assert eff["skip_llm_eval"] is True
        assert eff["over_cap"] is False

    def test_floor_still_over_cap_flagged(self) -> None:
        out = estimate_with_degradation(
            "director", 4, cap_usd=0.0001, router=_FakeRouter(),
        )
        eff = out["effective"]
        assert eff["over_cap"] is True
        assert eff["degradation_steps"] == [
            "director_to_single", "skip_llm_eval",
        ]

    def test_single_mode_skips_step1(self) -> None:
        single = estimate_chapter("single", router=_FakeRouter())
        out = estimate_with_degradation(
            "single", cap_usd=single["estimated_usd"] / 2,
            router=_FakeRouter(),
        )
        assert out["effective"]["degradation_steps"] == ["skip_llm_eval"]
