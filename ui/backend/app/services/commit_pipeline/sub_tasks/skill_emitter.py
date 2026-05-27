"""Skill-emitter sub-task — Phase 1 stub (opt-in, ``default_enabled=False``).

Phase 5 implements: learn new writing skills from user edit deltas and
emit ``SKILL_<name>.md`` files via the existing reference_ingest
SkillEmitter. See spec § 模块 1 (skill_emitter row).
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from . import SubTaskContext


async def run(ctx: "SubTaskContext") -> dict[str, Any]:
    return {"status": "stub", "task": "skill_emitter"}
