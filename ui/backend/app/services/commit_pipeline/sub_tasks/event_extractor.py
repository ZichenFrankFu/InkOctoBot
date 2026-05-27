"""Event-extractor sub-task — Phase 1 stub.

Phase 2 implements: extract 0-5 key events per chapter and INSERT into
``episodic_events`` with ``generated_by='llm_auto'`` + ``llm_model``.
See spec § 模块 3.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from . import SubTaskContext


async def run(ctx: "SubTaskContext") -> dict[str, Any]:
    return {"status": "stub", "task": "event_extractor"}
