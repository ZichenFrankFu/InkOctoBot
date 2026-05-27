"""Snapshot-detector sub-task — Phase 1 stub.

Phase 4 implements: detect pending character snapshot transitions
satisfied by the new chapter. ``services/snapshot_auto_detector.py``
already has ``detect_after_chapter_commit`` — Phase 4 just needs to
wire it in here and route results into ``character_snapshot_reminders``
+ notifications. See spec § 模块 7.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from . import SubTaskContext


async def run(ctx: "SubTaskContext") -> dict[str, Any]:
    return {"status": "stub", "task": "snapshot_detector"}
