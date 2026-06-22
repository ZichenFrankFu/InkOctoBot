"""Dynamic character-budget allocator (LOADER_SPEC v3.1).

Each loader declares ``(minimum, target, maximum)`` character budgets
and a priority tier. The allocator looks at the *natural* length every
active loader would emit at no cap and decides what each gets:

- **Case A** — ``sum(natural) ≤ total_budget``: give every loader its
  natural length (capped at its own maximum just in case).

- **Case B** — ``total_budget < sum(natural) ≤ sum(maximum)``: scale
  each loader's share by the ratio of its target to ``sum(target)``,
  but never exceed ``maximum``. Any leftover budget redistributes
  proportionally among loaders that haven't yet hit their max.

- **Case C** — ``sum(natural) > sum(maximum)``: tier-based fallback.
  First every active loader is granted its ``minimum``. Remaining
  budget is then handed out tier by tier (1 → 4): each tier climbs
  to ``target`` before the next tier touches its minimum.  After all
  tiers reach their target, remaining budget continues climbing
  toward ``maximum`` in the same priority order.

The configuration table ``LOADER_BUDGETS`` is the canonical source for
each block's ``(min, target, max, tier)``. Loader ``plan()`` functions
pull from this table so allocator + loaders stay in sync.
"""
from __future__ import annotations

from typing import Iterable

from .loader_protocol import LoaderPlan


# Per-loader budget configuration. Keep this in lockstep with the
# loader files themselves — each ``plan()`` reads from here.
#
# Values follow the spec's 预算画像表 (All_Function_Expected v3.1
# § 3.3.6). Block-id ↔ spec-loader name mapping:
#   platform_directive = platform_style (loader 2)
#   subplots           = plotline       (loader 12)
#   market_overview    = market_overview (loader 1, 开书助手 only)
LOADER_BUDGETS: dict[str, dict[str, int]] = {
    # tier 1 — 核心
    "chapter_outline":            {"min":  600, "target": 1200, "max": 1800, "tier": 1},
    "user_special_requirements":  {"min":  300, "target":  600, "max": 1000, "tier": 1},
    "character_cards":            {"min":  800, "target": 1800, "max": 2800, "tier": 1},
    "storyland_state":            {"min":  800, "target": 2000, "max": 3000, "tier": 1},
    "current_chapter_draft":      {"min": 1500, "target": 4000, "max": 6000, "tier": 1},
    # tier 2 — 重要
    "reader_memory":              {"min": 1500, "target": 4500, "max": 6000, "tier": 2},
    "worldbook":                  {"min":  600, "target": 1600, "max": 2400, "tier": 2},
    "subplots":                   {"min":  500, "target": 1200, "max": 1800, "tier": 2},
    "foreshadowing":              {"min":  300, "target":  800, "max": 1200, "tier": 2},
    "skills":                     {"min":  800, "target": 2400, "max": 3600, "tier": 2},
    # tier 3 — 补充
    "reference":                  {"min":  800, "target": 2400, "max": 3600, "tier": 3},
    "inspiration":                {"min":  200, "target":  600, "max":  900, "tier": 3},
    "platform_directive":         {"min":  200, "target":  900, "max": 1400, "tier": 3},
    "user_preferences":           {"min":  200, "target":  500, "max":  800, "tier": 3},
    # tier 4 — 系统
    "market_overview":            {"min":  600, "target": 1500, "max": 2000, "tier": 4},
}


# Sums over the WHOLE table (incl. market_overview, which only the
# 开书助手 profile activates). The per-agent default budget is computed
# from its own profile in builder.py — the writer profile lands on the
# spec's ≈24K total.
TOTAL_TARGET = sum(c["target"] for c in LOADER_BUDGETS.values())
TOTAL_MAXIMUM = sum(c["max"] for c in LOADER_BUDGETS.values())
TOTAL_MINIMUM = sum(c["min"] for c in LOADER_BUDGETS.values())


def _scale_with_caps(
    plans: list[LoaderPlan], total_budget: int,
) -> dict[str, int]:
    """Case B helper — scale by target proportion, respecting per-plan
    maximum. Leftover budget loops back to plans that still have headroom.
    """
    out: dict[str, int] = {}
    total_target = sum(p.target for p in plans)
    if total_target == 0:
        # Edge case — every active plan has target 0; allocate flat.
        share = total_budget // max(1, len(plans))
        for p in plans:
            out[p.block_id] = min(share, p.maximum)
        return out

    remaining = total_budget
    pending = list(plans)
    while pending and remaining > 0:
        scaled = {
            p.block_id: int(remaining * (p.target / sum(q.target for q in pending)))
            for p in pending
        }
        new_pending: list[LoaderPlan] = []
        consumed = 0
        for p in pending:
            give = min(scaled[p.block_id], p.maximum)
            out[p.block_id] = out.get(p.block_id, 0) + give
            consumed += give
            if out[p.block_id] < p.maximum:
                new_pending.append(p)
        # If no one consumed anything (e.g. all hit max), stop.
        if consumed == 0:
            break
        remaining -= consumed
        if new_pending == pending:
            # Same set still pending with nothing left to give to others;
            # break to avoid an infinite loop.
            break
        pending = new_pending
    return out


def _tier_climb(
    plans: list[LoaderPlan], total_budget: int,
) -> dict[str, int]:
    """Case C helper — minimum-first, then climb to target by tier, then
    to maximum by tier."""
    out: dict[str, int] = {p.block_id: p.minimum for p in plans}
    remaining = total_budget - sum(out.values())
    if remaining <= 0:
        # Degenerate case (spec 机制5): even the minimums exceed the
        # budget — scale every loader's minimum by the same ratio;
        # tier priority does not apply here.
        if total_budget <= 0:
            return {p.block_id: 0 for p in plans}
        scale = total_budget / max(1, sum(out.values()))
        return {pid: max(0, int(v * scale)) for pid, v in out.items()}

    # Stage 1: climb to target tier by tier.
    for tier in (1, 2, 3, 4):
        in_tier = [p for p in plans if p.priority_tier == tier]
        for p in in_tier:
            gap = p.target - out[p.block_id]
            if gap <= 0:
                continue
            give = min(gap, remaining)
            out[p.block_id] += give
            remaining -= give
            if remaining <= 0:
                return out

    # Stage 2: climb to maximum tier by tier (same priority order).
    for tier in (1, 2, 3, 4):
        in_tier = [p for p in plans if p.priority_tier == tier]
        for p in in_tier:
            gap = p.maximum - out[p.block_id]
            if gap <= 0:
                continue
            give = min(gap, remaining)
            out[p.block_id] += give
            remaining -= give
            if remaining <= 0:
                return out
    return out


def allocate(
    plans: Iterable[LoaderPlan], total_budget: int = TOTAL_TARGET,
) -> dict[str, int]:
    """Distribute ``total_budget`` characters across the active plans.

    Returns ``{block_id: allocated_chars}``. Inactive loaders (those
    that didn't return a plan) are absent from the result.
    """
    plans = list(plans)
    if not plans:
        return {}
    total_natural = sum(p.natural_length for p in plans)
    total_max = sum(p.maximum for p in plans)

    # Case A — comfortable
    if total_natural <= total_budget:
        return {p.block_id: min(p.natural_length, p.maximum) for p in plans}
    # Case B — over target but under absolute ceiling
    if total_natural <= total_max:
        return _scale_with_caps(plans, total_budget)
    # Case C — over ceiling, fall back to tier climbing
    return _tier_climb(plans, total_budget)
