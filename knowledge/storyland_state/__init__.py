"""knowledge.storyland_state — StorylandState system (state authority, modeled on InkOS).

See `docs/storyland_state_system.md` for the architecture overview,
end-to-end data flow, validation rules and verification guide.

Six canonical state files unified under StorylandStateStore:
  current_state / particle_ledger / pending_hooks / chapter_summaries /
  subplot_board / emotional_arcs

(The legacy ``character_matrix`` was removed in v3.0 — pairwise
relationships now live on ``character_snapshots.relations_overrides``.
See docs/LOADER_SPEC.md, Loaders 5 + 10.)

SQLite is canonical; Markdown is an on-demand export view.
"""
from __future__ import annotations

from knowledge.storyland_state.schemas import (
    # enums
    StorylandStateKind, HookStatus, HookImportance, SubplotStatus,
    # deltas
    SPOTriple, StatePatch, NumericalReconciliation,
    HookDelta, ChapterSummaryDelta, SubplotUpdate,
    EmotionArcEntry,
    StorylandStateDeltas, ApplyResult,
    # validation
    ValidationIssue, ValidationReport,
)

__all__ = [
    "StorylandStateKind", "HookStatus", "HookImportance", "SubplotStatus",
    "SPOTriple", "StatePatch", "NumericalReconciliation",
    "HookDelta", "ChapterSummaryDelta", "SubplotUpdate",
    "EmotionArcEntry",
    "StorylandStateDeltas", "ApplyResult",
    "ValidationIssue", "ValidationReport",
]
