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
LOADER_BUDGETS: dict[str, dict[str, int]] = {
    # tier 1 — must stay generous
    "chapter_outline":            {"min":  800, "target": 1200, "max": 2000, "tier": 1},
    "current_chapter_draft":      {"min": 1500, "target": 4000, "max": 6000, "tier": 1},
    "reader_memory":              {"min": 1500, "target": 4500, "max": 6500, "tier": 1},
    "character_cards":            {"min":  800, "target": 1800, "max": 3000, "tier": 1},
    # tier 2 — important
    "storyland_state":            {"min":  500, "target": 2000, "max": 3000, "tier": 2},
    "foreshadowing":              {"min":  300, "target":  800, "max": 1500, "tier": 2},
    "subplots":                   {"min":  300, "target": 1200, "max": 2000, "tier": 2},
    "skills":                     {"min":  500, "target": 2400, "max": 3500, "tier": 2},
    # tier 3 — supplementary
    "worldbook":                  {"min":  300, "target": 1600, "max": 2500, "tier": 3},
    "reference":                  {"min":  400, "target": 2400, "max": 3500, "tier": 3},
    "inspiration":                {"min":  150, "target":  800, "max": 1500, "tier": 3},
    "user_special_requirements":  {"min":  100, "target":  600, "max": 1500, "tier": 3},
    # tier 4 — system
    "platform_directive":         {"min":   50, "target":  250, "max":  500, "tier": 4},
    "user_preferences":           {"min":  100, "target":  500, "max":  800, "tier": 4},
}


TOTAL_TARGET = sum(c["target"] for c in LOADER_BUDGETS.values())   # ≈ 24K
TOTAL_MAXIMUM = sum(c["max"] for c in LOADER_BUDGETS.values())     # ≈ 39K
TOTAL_MINIMUM = sum(c["min"] for c in LOADER_BUDGETS.values())     # ≈ 7K


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
        # Even minimums exceed the budget — clip every loader to its
        # share so the total fits. Tier 1 keeps full min; lower tiers
        # get progressively trimmed.
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
