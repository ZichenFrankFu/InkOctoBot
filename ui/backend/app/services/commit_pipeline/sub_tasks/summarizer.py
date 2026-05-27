"""Chapter-summary sub-task — Phase 1 stub.

Phase 2 implements: LLM-generated reader-perspective summary (title +
100-300 字 body) written into ``chapter_summaries`` with
``generated_by='llm_auto'``. See spec § 模块 2.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from . import SubTaskContext


async def run(ctx: "SubTaskContext") -> dict[str, Any]:
    return {"status": "stub", "task": "chapter_summarizer"}
