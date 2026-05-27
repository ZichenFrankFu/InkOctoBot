"""Per-block soft character budgets (LOADER_SPEC v3.1).

The canonical configuration lives in
``ui/backend/app/services/prompt_context/budget_allocator.py``:
``LOADER_BUDGETS[<block_id>] = {min, target, max, tier}``. Each loader
queries its own row from there and exports a ``LoaderPlan`` with the
three-tier numbers.

This module exposes the legacy flat ``BUDGETS`` dict — a back-compat
view that maps every block to its **target** budget. Loaders that
still call ``clip(text, BUDGETS["foo"])`` directly continue to work
without code change; the dynamic allocator runs above them.
"""
from __future__ import annotations

from .budget_allocator import LOADER_BUDGETS


BUDGETS: dict[str, int] = {
    bid: cfg["target"]
    for bid, cfg in LOADER_BUDGETS.items()
}

# Extra budgets used by helpers that live OUTSIDE the loader chain
# (the single-agent template builds ``referenced_materials`` directly
# rather than through a loader).
BUDGETS["referenced_materials"] = 2600
