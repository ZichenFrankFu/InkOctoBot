"""State-extractor sub-task — Phase 1 stub.

Phase 3 implements: LLM extracts state/ledger/emotion deltas + new
entities, writes **directly** into truth_current_state / ledger_changes
/ emotion_arcs / storyland_entities (no staging table), mirrored to an
append-only ``state_change_log`` for audit. Entity creation runs
through 5 filtering layers; suspected-duplicate entities go to
``entity_review_queue``. See spec § 模块 5 + 6.

Risk flagged in plan: ``truth_current_state`` already has
``valid_from_chapter`` / ``valid_to_chapter`` columns — Phase 3 must
decide whether to DROP them or implement temporal supersession.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from . import SubTaskContext


async def run(ctx: "SubTaskContext") -> dict[str, Any]:
    return {"status": "stub", "task": "state_extractor"}
