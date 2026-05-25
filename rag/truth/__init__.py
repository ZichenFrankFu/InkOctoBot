"""rag.truth — Truth File system (state authority, modeled on InkOS).

Seven canonical truth files unified under TruthFileStore:
  current_state / particle_ledger / pending_hooks / chapter_summaries /
  subplot_board / emotional_arcs / character_matrix

SQLite is canonical; Markdown is an on-demand export view.
"""
from __future__ import annotations

from rag.truth.schemas import (
    # enums
    TruthFileKind, HookStatus, HookImportance, SubplotStatus,
    # deltas
    SPOTriple, StatePatch, NumericalReconciliation,
    HookDelta, ChapterSummaryDelta, SubplotUpdate,
    EmotionArcEntry, RelationUpdate,
    TruthDeltas, ApplyResult,
    # validation
    ValidationIssue, ValidationReport,
)

__all__ = [
    "TruthFileKind", "HookStatus", "HookImportance", "SubplotStatus",
    "SPOTriple", "StatePatch", "NumericalReconciliation",
    "HookDelta", "ChapterSummaryDelta", "SubplotUpdate",
    "EmotionArcEntry", "RelationUpdate",
    "TruthDeltas", "ApplyResult",
    "ValidationIssue", "ValidationReport",
]
