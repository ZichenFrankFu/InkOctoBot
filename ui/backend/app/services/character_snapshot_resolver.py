"""CharacterSnapshotResolver — pick the right snapshot view for a chapter.

LOADER_SPEC Loader 5, Batch 5.

Given a character + a chapter number, returns one of four transition
states + the relevant snapshot rows. The character_cards loader uses
this to decide what to render:

- ``stable``                — the last fully-completed snapshot is the
                              "current" state. No transition is in
                              progress for this chapter.
- ``transition_event``      — the chapter is in a snapshot's
                              ``bound_chapters`` list and the snapshot
                              isn't complete yet. This is a beat of the
                              transition.
- ``transition_gap``        — the chapter falls between the first and
                              last bound chapter of an in-progress
                              snapshot but isn't itself a bound chapter.
                              Character is mid-transition but the
                              chapter isn't a transition beat — show
                              wavering / interim behavior.
- ``transition_complete``   — the chapter is exactly the snapshot's
                              ``transition_complete_chapter``. The
                              transition finishes here; the snapshot
                              becomes the new baseline going forward.

Returns ``{"baseline_snapshot": ..., "in_transition": ...,
"transition_status": "...", "previous_snapshot": ...}``.

``baseline_snapshot`` is the most-recently-completed snapshot whose
``transition_complete_chapter <= chapter_num`` (None when the character
hasn't had any snapshot complete yet — the base character card is the
baseline).

``previous_snapshot`` is the snapshot one ``snapshot_order`` step back
from ``in_transition`` (so the renderer can describe "from X to Y").
"""
from __future__ import annotations

import logging
from typing import Any

from . import snapshot_store

logger = logging.getLogger("inkoctobot.services.character_snapshot_resolver")


_STABLE = "stable"
_EVENT = "transition_event"
_GAP = "transition_gap"
_COMPLETE = "transition_complete"


def _snapshots_ordered(db_path: str, character_id: str) -> list[dict]:
    return snapshot_store.list_snapshots(db_path, character_id)


def _pick_baseline(snapshots: list[dict], chapter_num: int) -> dict | None:
    """Most-recently-completed snapshot at or before ``chapter_num``."""
    completed = [
        s for s in snapshots
        if s.get("transition_complete_chapter") is not None
        and int(s["transition_complete_chapter"]) <= chapter_num
    ]
    if not completed:
        return None
    completed.sort(key=lambda s: int(s["transition_complete_chapter"]))
    return completed[-1]


def _pick_in_transition(
    snapshots: list[dict], chapter_num: int,
) -> tuple[dict | None, str]:
    """Return (snapshot, status) for the in-progress snapshot, or (None, 'stable')."""
    # Priority 1: transition_complete_chapter == chapter_num
    for s in snapshots:
        comp = s.get("transition_complete_chapter")
        if comp is not None and int(comp) == chapter_num:
            return s, _COMPLETE
    # Priority 2: chapter_num is in bound_chapters AND not past complete
    for s in snapshots:
        bound = [int(c) for c in (s.get("bound_chapters") or [])]
        if not bound:
            continue
        comp = s.get("transition_complete_chapter")
        if chapter_num in bound:
            if comp is None or chapter_num < int(comp):
                return s, _EVENT
    # Priority 3: chapter_num is within [min(bound), max(bound)] but
    # not in the list (gap between beats).
    for s in snapshots:
        bound = [int(c) for c in (s.get("bound_chapters") or [])]
        if len(bound) < 2:
            continue
        comp = s.get("transition_complete_chapter")
        lo, hi = min(bound), max(bound)
        if lo <= chapter_num <= hi and chapter_num not in bound:
            if comp is None or chapter_num < int(comp):
                return s, _GAP
    return None, _STABLE


def _previous_of(
    snapshots: list[dict], in_transition: dict | None,
) -> dict | None:
    if in_transition is None:
        return None
    prev_order = int(in_transition["snapshot_order"]) - 1
    if prev_order < 1:
        return None
    for s in snapshots:
        if int(s["snapshot_order"]) == prev_order:
            return s
    return None


def resolve(
    db_path: str, character_id: str, chapter_num: int,
) -> dict[str, Any]:
    """Compute the snapshot view for ``character_id`` at ``chapter_num``.

    See module docstring for the four-state contract.
    """
    snaps = _snapshots_ordered(db_path, character_id)
    baseline = _pick_baseline(snaps, chapter_num)
    in_trans, status = _pick_in_transition(snaps, chapter_num)
    previous = _previous_of(snaps, in_trans)
    return {
        "baseline_snapshot":   baseline,
        "in_transition":       in_trans,
        "transition_status":   status,
        "previous_snapshot":   previous,
    }
