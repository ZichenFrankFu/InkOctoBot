"""Pydantic schemas for the Truth File system.

All delta models are frozen for immutability. StorylandStateDeltas is the top-level
bundle applied atomically by StorylandStateStore.apply_deltas.
"""
from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


# ──────────────────────────────────────────────────────────────
# Enums
# ──────────────────────────────────────────────────────────────

class StorylandStateKind(str, Enum):
    current_state     = "current_state"
    particle_ledger   = "particle_ledger"
    pending_hooks     = "pending_hooks"
    chapter_summaries = "chapter_summaries"
    subplot_board     = "subplot_board"
    emotional_arcs    = "emotional_arcs"


class HookStatus(str, Enum):
    open         = "open"
    progressing  = "progressing"
    pressured    = "pressured"
    near_payoff  = "near_payoff"
    resolved     = "resolved"
    abandoned    = "abandoned"


class HookImportance(str, Enum):
    A = "A"
    B = "B"
    C = "C"


class SubplotStatus(str, Enum):
    setup      = "setup"
    building   = "building"
    climax     = "climax"
    resolution = "resolution"
    dormant    = "dormant"


# ──────────────────────────────────────────────────────────────
# 1. current_state — SPO triples + chapter validity windows
# ──────────────────────────────────────────────────────────────

class SPOTriple(BaseModel):
    """A subject-predicate-object fact valid over a chapter range."""
    model_config = ConfigDict(frozen=True)
    subject: str
    predicate: str
    object: str
    valid_from_chapter: int
    valid_to_chapter: int | None = None
    confidence: float = 1.0


SubjectType = Literal["character", "location", "item", "organization", "concept"]


class StatePatch(BaseModel):
    """A delta-form mutation to current_state."""
    model_config = ConfigDict(frozen=True)
    subject: str
    predicate: str
    object: str
    action: Literal["upsert", "invalidate"] = "upsert"
    valid_from_chapter: int
    # LOADER_SPEC Loader 10: tag the subject's entity type so the
    # loader can render state grouped by character / location / etc.
    # Default 'character' keeps every existing call site working.
    subject_type: SubjectType = "character"


# ──────────────────────────────────────────────────────────────
# 2. particle_ledger — resource/item accounting (closed equations)
# ──────────────────────────────────────────────────────────────

class NumericalReconciliation(BaseModel):
    """One bookkeeping entry: old + delta == new (or set => new directly)."""
    model_config = ConfigDict(frozen=True)
    character: str
    resource: str
    old_value: int
    operation: Literal["add", "subtract", "set"]
    delta: int
    new_value: int
    reason: str
    in_text_evidence: str    # <= 80 chars; validator enforces


# ──────────────────────────────────────────────────────────────
# 3. pending_hooks — foreshadow state machine
# ──────────────────────────────────────────────────────────────

class HookDelta(BaseModel):
    """Mutation to a hook (creation or state transition)."""
    model_config = ConfigDict(frozen=True)
    hook_id: str | None = None   # None means a new hook will be created
    description: str
    action: Literal["new", "mention", "progress", "resolve", "abandon"]
    importance: HookImportance = HookImportance.B
    expected_payoff_chapter: int | None = None
    evidence: str = ""
    # A1 (InkOS) — spoiler filter. Defaults preserve previous shape.
    # When ``is_spoiler=True`` and a renderer is called with a
    # ``pov_character`` filter that's not in ``revealed_to_chars``, the
    # hook is omitted from the bundle. Plumbing in render_pending_hooks.
    is_spoiler: bool = False
    revealed_to_chars: list[str] = Field(default_factory=list)


# ──────────────────────────────────────────────────────────────
# 4. chapter_summaries — per-chapter recap (re-used via adapter)
# ──────────────────────────────────────────────────────────────

class ChapterSummaryDelta(BaseModel):
    model_config = ConfigDict(frozen=True)
    summary: str
    key_events: list[str] = Field(default_factory=list)
    pov_character: str | None = None
    mood: str | None = None


# ──────────────────────────────────────────────────────────────
# 5. subplot_board — parallel narrative threads
# ──────────────────────────────────────────────────────────────

class SubplotUpdate(BaseModel):
    model_config = ConfigDict(frozen=True)
    thread_id: str | None = None    # None means a new thread will be created
    name: str
    action: Literal["new", "advance", "climax", "resolve", "dormant"]
    status_after: SubplotStatus
    related_hook_ids: list[str] = Field(default_factory=list)
    note: str = ""


# ──────────────────────────────────────────────────────────────
# 6. emotional_arcs — character emotion trajectories
# ──────────────────────────────────────────────────────────────

class EmotionArcEntry(BaseModel):
    model_config = ConfigDict(frozen=True)
    character: str
    from_state: str
    to_state: str
    trigger: str


# ──────────────────────────────────────────────────────────────
# Top-level bundle + apply result
# ──────────────────────────────────────────────────────────────

class StorylandStateDeltas(BaseModel):
    """Atomic bundle of all-file deltas for one chapter application."""
    model_config = ConfigDict(frozen=True)
    chapter_num: int
    current_state_patches: list[StatePatch] = Field(default_factory=list)
    particle_reconciliations: list[NumericalReconciliation] = Field(default_factory=list)
    hook_deltas: list[HookDelta] = Field(default_factory=list)
    chapter_summary: ChapterSummaryDelta | None = None
    subplot_updates: list[SubplotUpdate] = Field(default_factory=list)
    emotion_arc_entries: list[EmotionArcEntry] = Field(default_factory=list)


class ValidationIssue(BaseModel):
    """One validator finding."""
    rule_id: str
    severity: Literal["error", "warning", "info"]
    message: str
    location: str = ""


class ValidationReport(BaseModel):
    """Result of validate_state / validate_deltas."""
    passed: bool
    issues: list[ValidationIssue] = Field(default_factory=list)
    stats: dict[str, int] = Field(default_factory=dict)


class ApplyResult(BaseModel):
    """Outcome of StorylandStateStore.apply_deltas."""
    success: bool
    chapter_num: int
    applied_counts: dict[str, int] = Field(default_factory=dict)
    cross_ref_issues: list[ValidationIssue] = Field(default_factory=list)
    sqlite_errors: list[str] = Field(default_factory=list)
    deltas_hash: str = ""
    idempotent_hit: bool = False
