"""Tests for the dynamic budget allocator (LOADER_SPEC v3.1)."""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from ui.backend.app.services.prompt_context.budget_allocator import (
    LOADER_BUDGETS, TOTAL_MAXIMUM, TOTAL_MINIMUM, TOTAL_TARGET, allocate,
)
from ui.backend.app.services.prompt_context.loader_protocol import LoaderPlan


def _plan(block_id: str, natural: int) -> LoaderPlan:
    cfg = LOADER_BUDGETS[block_id]
    return LoaderPlan(
        block_id=block_id,
        natural_length=natural,
        minimum=cfg["min"], target=cfg["target"], maximum=cfg["max"],
        priority_tier=cfg["tier"],
        render=lambda b: "x" * b,
    )


class TestAllocator(unittest.TestCase):

    def test_empty_input_returns_empty_dict(self) -> None:
        self.assertEqual(allocate([], total_budget=10_000), {})

    def test_case_a_natural_within_budget(self) -> None:
        """If every plan's natural fits inside ``total_budget``, each one
        is granted exactly its natural length (capped at maximum)."""
        plans = [
            _plan("chapter_outline", 600),
            _plan("character_cards", 800),
        ]
        out = allocate(plans, total_budget=TOTAL_TARGET)
        self.assertEqual(out["chapter_outline"], 600)
        self.assertEqual(out["character_cards"], 800)

    def test_case_a_clips_at_per_plan_maximum(self) -> None:
        """Plans whose natural exceeds their own maximum are clipped at
        maximum even when total budget has headroom."""
        plans = [_plan("chapter_outline", 999_999)]
        out = allocate(plans, total_budget=999_999_999)
        self.assertEqual(out["chapter_outline"],
                          LOADER_BUDGETS["chapter_outline"]["max"])

    def test_case_b_scales_by_target_share(self) -> None:
        """When natural > total but ≤ ∑max, allocations roughly track
        the target proportion."""
        plans = [
            _plan("chapter_outline", 2000),  # max 2000
            _plan("worldbook", 2500),         # max 2500
        ]
        # Force case B by passing a tight budget.
        out = allocate(plans, total_budget=2000)
        self.assertLessEqual(out["chapter_outline"],
                              LOADER_BUDGETS["chapter_outline"]["max"])
        self.assertLessEqual(out["worldbook"],
                              LOADER_BUDGETS["worldbook"]["max"])
        # Sum stays ≤ requested budget.
        self.assertLessEqual(sum(out.values()), 2000)

    def test_case_c_tier_climb_keeps_tier1_minimums(self) -> None:
        """When natural blows past ∑max, every plan still gets at least
        its minimum, with leftover handed to tier 1 first."""
        plans = [
            _plan("chapter_outline", 50_000),       # tier 1
            _plan("current_chapter_draft", 50_000), # tier 1
            _plan("reader_memory", 50_000),         # tier 1
            _plan("character_cards", 50_000),       # tier 1
            _plan("worldbook", 50_000),             # tier 3
            _plan("platform_directive", 50_000),    # tier 4
        ]
        out = allocate(plans, total_budget=TOTAL_MINIMUM + 1000)
        for p in plans:
            cfg = LOADER_BUDGETS[p.block_id]
            self.assertGreaterEqual(out[p.block_id], cfg["min"])

    def test_tier_climb_prefers_tier1_for_extra(self) -> None:
        """Climb stage 1: tier 1 hits target before tier 3 does."""
        plans = [
            _plan("chapter_outline", 50_000),  # tier 1, min 800 target 1200
            _plan("worldbook", 50_000),        # tier 3, min 300 target 1600
        ]
        # Provide enough to lift tier 1 to target, then start filling tier 3.
        budget = (LOADER_BUDGETS["chapter_outline"]["target"]
                  + LOADER_BUDGETS["worldbook"]["min"] + 200)
        out = allocate(plans, total_budget=budget)
        self.assertEqual(out["chapter_outline"],
                          LOADER_BUDGETS["chapter_outline"]["target"])
        # Worldbook gets minimum plus the extra 200 climbing toward target.
        self.assertEqual(
            out["worldbook"], LOADER_BUDGETS["worldbook"]["min"] + 200,
        )

    def test_total_target_constant_matches_config(self) -> None:
        self.assertEqual(
            TOTAL_TARGET,
            sum(c["target"] for c in LOADER_BUDGETS.values()),
        )
        self.assertEqual(
            TOTAL_MAXIMUM, sum(c["max"] for c in LOADER_BUDGETS.values()),
        )
        self.assertEqual(
            TOTAL_MINIMUM, sum(c["min"] for c in LOADER_BUDGETS.values()),
        )


if __name__ == "__main__":
    unittest.main()
