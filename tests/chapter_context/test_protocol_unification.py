"""Stage 3 Tasks 3.1 + 3.2: LoaderPlan ↔ BlockProvider unification.

Two parallel block protocols existed before Stage 3:
  - LoaderPlan  (services/prompt_context/loader_protocol.py) — used
                 by build_generation_context to assemble the 14-loader
                 RAG output for single-agent generation
  - BlockProvider (agents/production/prompt_composer.py) — used by
                 multi-agent Writer to assemble prompts from 14 hand-
                 crafted block classes

Stage 3 bridges the two:
  - LoaderPlan.to_block_provider(allocated_budget) yields an adapter
    that satisfies the BlockProvider protocol
  - PromptComposer.add_loader_blocks(loader_plans) batch-imports a
    dict of LoaderPlan results as BlockProviders

Multi-agent agents can now consume the same 14-loader output the
single-agent pipeline uses, via the same protocol — no parallel
hierarchy.
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from agents.production.prompt_composer import (
    PromptComposer, PromptContext,
)
from ui.backend.app.services.prompt_context.loader_protocol import (
    LoaderPlan,
)


def _mk_plan(
    block_id: str, body: str, *,
    natural: int | None = None, tier: int = 3,
    target: int = 1000, mn: int = 100, mx: int = 2000,
) -> LoaderPlan:
    natural = natural if natural is not None else len(body) + 20

    def render(budget: int) -> str:
        return body[:max(0, budget)]

    return LoaderPlan(
        block_id=block_id,
        natural_length=natural,
        minimum=mn, target=target, maximum=mx,
        priority_tier=tier,
        render=render,
    )


# ─────────── Task 3.1: LoaderPlan.to_block_provider ───────────


class TestLoaderPlanAdapter(unittest.TestCase):

    def test_adapter_exposes_block_provider_interface(self) -> None:
        plan = _mk_plan("worldbook", "## 世界书\n灵气复苏", tier=3)
        bp = plan.to_block_provider()
        # BlockProvider protocol surface.
        self.assertEqual(bp.name, "worldbook")
        self.assertEqual(bp.role, "context")
        # render(ctx) ignores ctx — LoaderPlan captured its data at
        # plan() time.
        self.assertIn("灵气复苏", bp.render(PromptContext()))

    def test_tier_to_role_mapping(self) -> None:
        # tier 1 → user_content (chapter_outline / draft / etc)
        self.assertEqual(_mk_plan("a", "x", tier=1).to_block_provider().role,
                          "user_content")
        # tier 2 / 3 → context (RAG layer)
        self.assertEqual(_mk_plan("a", "x", tier=2).to_block_provider().role, "context")
        self.assertEqual(_mk_plan("a", "x", tier=3).to_block_provider().role, "context")
        # tier 4 → also context (system-level directive still lands in
        # context window via BaseAgent.invoke(context=...))
        self.assertEqual(_mk_plan("a", "x", tier=4).to_block_provider().role, "context")

    def test_explicit_allocation_respected(self) -> None:
        body = "甲" * 200
        plan = _mk_plan("worldbook", body, target=1000)
        # Default budget = target = 1000 → full body.
        self.assertEqual(len(plan.to_block_provider().render(PromptContext())),
                          len(body))
        # Explicit budget = 30 → clipped.
        clipped = plan.to_block_provider(30).render(PromptContext())
        self.assertEqual(len(clipped), 30)

    def test_render_failure_returns_empty(self) -> None:
        """Adapter swallows exceptions from the underlying render
        closure — a buggy loader shouldn't bring down the composer."""
        def bad_render(b: int) -> str:
            raise RuntimeError("loader bug")
        plan = LoaderPlan(
            block_id="bad", natural_length=100, minimum=10,
            target=100, maximum=200, priority_tier=2,
            render=bad_render,
        )
        bp = plan.to_block_provider()
        self.assertEqual(bp.render(PromptContext()), "")


# ─────────── Task 3.2: PromptComposer.add_loader_blocks ───────────


class TestPromptComposerLoaderIntegration(unittest.TestCase):

    def test_add_loader_blocks_batch_registers(self) -> None:
        plans = {
            "worldbook":        _mk_plan("worldbook", "## 世界书\n灵气", tier=3),
            "character_cards":  _mk_plan("character_cards", "## 角色\n主角", tier=1),
            "chapter_outline":  None,   # inactive — must be skipped
        }
        composer = PromptComposer([])
        composer.add_loader_blocks(plans)
        # 2 providers added (None skipped).
        self.assertEqual(len(composer.providers), 2)
        names = {p.name for p in composer.providers}
        self.assertEqual(names, {"worldbook", "character_cards"})

    def test_loader_blocks_split_by_role_in_composed_output(self) -> None:
        plans = {
            "chapter_outline": _mk_plan(
                "chapter_outline", "## 大纲\n章节剧情", tier=1,
            ),
            "worldbook": _mk_plan(
                "worldbook", "## 世界书\n灵气体系", tier=3,
            ),
        }
        composer = PromptComposer([])
        composer.add_loader_blocks(plans)
        ctx = PromptContext()
        user = composer.build_user_content(ctx)
        context = composer.build_context(ctx)
        # tier=1 → user_content
        self.assertIn("章节剧情", user)
        self.assertNotIn("章节剧情", context)
        # tier=3 → context
        self.assertIn("灵气体系", context)
        self.assertNotIn("灵气体系", user)

    def test_explicit_allocations_override_target(self) -> None:
        plan = _mk_plan("worldbook", "甲" * 500, tier=3, target=500)
        composer = PromptComposer([])
        # Force allocation to 50 chars.
        composer.add_loader_blocks(
            {"worldbook": plan}, allocations={"worldbook": 50},
        )
        context = composer.build_context(PromptContext())
        # The body was 500 chars; with budget=50 we expect ≤ 50.
        self.assertLessEqual(len(context), 50)

    def test_skips_non_loader_plan_values(self) -> None:
        """Defensive: caller passes a dict that includes a stray
        non-LoaderPlan value — must not crash."""
        composer = PromptComposer([])
        composer.add_loader_blocks({
            "worldbook": _mk_plan("worldbook", "ok", tier=3),
            "garbage":   "not a LoaderPlan",
            "none":      None,
        })
        self.assertEqual(len(composer.providers), 1)

    def test_add_block_still_works(self) -> None:
        """Sanity: the legacy add_block path is unchanged."""
        from agents.production.prompt_composer import FnBlock
        composer = PromptComposer([])
        composer.add_block(
            FnBlock(name="custom", role="user_content",
                    fn=lambda c: "custom block"),
        )
        self.assertEqual(len(composer.providers), 1)
        self.assertEqual(composer.build_user_content(PromptContext()),
                          "custom block")


if __name__ == "__main__":
    unittest.main()
