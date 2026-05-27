"""ChromaDB-indexer sub-task — Phase 1 stub.

Phase 2 implements: chunk chapter text (400-char chunks, 80-char
overlap) → embed via :class:`EmbeddingService` → upsert into the
``project_{id}_chunks_{model_key}`` collection.

Risk flagged in plan: current ``knowledge/vector_store.py`` collections
have zero project-scope. Phase 2 design must decide the migration
strategy before implementation. See spec § 模块 4.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from . import SubTaskContext


async def run(ctx: "SubTaskContext") -> dict[str, Any]:
    return {"status": "stub", "task": "chromadb_indexer"}
