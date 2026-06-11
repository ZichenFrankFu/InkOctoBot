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
    # Priority 3: chapter_num is inside the transition window but not a
    # beat chapter. spec 角色卡·机制1: 关键帧 N 与 N+1 之间都是过渡期 —
    # the window runs from the first beat up to (and excluding) the
    # completion chapter, so beats-to-completion chapters also count.
    for s in snapshots:
        bound = [int(c) for c in (s.get("bound_chapters") or [])]
        if not bound:
            continue
        comp = s.get("transition_complete_chapter")
        lo, hi = min(bound), max(bound)
        if comp is not None:
            hi = max(hi, int(comp) - 1)
        elif len(bound) < 2:
            continue   # single beat without completion → no window
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


# 角色卡·机制2: 转变态 3 档位
_STAGE_WAVERING = "wavering"   # 动摇 — 对关键帧 N 的怀疑
_STAGE_PROBING = "probing"     # 试探 — 尝试关键帧 N+1 行为的效果
_STAGE_LEANING = "leaning"     # 倾向 — 承认 N+1 更有用但尚未切换
_VALID_STAGES = {_STAGE_WAVERING, _STAGE_PROBING, _STAGE_LEANING}

STAGE_LABELS = {
    _STAGE_WAVERING: "动摇",
    _STAGE_PROBING:  "试探",
    _STAGE_LEANING:  "倾向",
}


def _transition_stage(
    snapshot: dict, chapter_num: int,
) -> tuple[str, str]:
    """(stage, source) for a chapter inside a transition window.

    User-set per-chapter 档位 wins (``transition_stages``); otherwise
    derive from progress through [window start, complete chapter]:
    first third → 动摇, middle → 试探, final third → 倾向.
    """
    stages = snapshot.get("transition_stages") or {}
    explicit = stages.get(str(chapter_num)) or stages.get(chapter_num)
    if explicit in _VALID_STAGES:
        return explicit, "user"

    bound = [int(c) for c in (snapshot.get("bound_chapters") or [])]
    comp = snapshot.get("transition_complete_chapter")
    start = min(bound) if bound else chapter_num
    end = int(comp) if comp is not None else (max(bound) if bound else chapter_num)
    if end <= start:
        return _STAGE_PROBING, "derived"
    progress = (chapter_num - start) / (end - start)
    if progress < 1 / 3:
        return _STAGE_WAVERING, "derived"
    if progress < 2 / 3:
        return _STAGE_PROBING, "derived"
    return _STAGE_LEANING, "derived"


def resolve(
    db_path: str, character_id: str, chapter_num: int,
) -> dict[str, Any]:
    """Compute the snapshot view for ``character_id`` at ``chapter_num``.

    See module docstring for the four-state contract. While in a
    transition (event / gap), ``transition_stage`` carries the 3-档位
    (动摇 / 试探 / 倾向, 角色卡·机制2) with ``transition_stage_source``
    telling whether the user set it or it was derived from progress.
    """
    snaps = _snapshots_ordered(db_path, character_id)
    baseline = _pick_baseline(snaps, chapter_num)
    in_trans, status = _pick_in_transition(snaps, chapter_num)
    previous = _previous_of(snaps, in_trans)
    out: dict[str, Any] = {
        "baseline_snapshot":   baseline,
        "in_transition":       in_trans,
        "transition_status":   status,
        "previous_snapshot":   previous,
    }
    if in_trans is not None and status in (_EVENT, _GAP):
        stage, source = _transition_stage(in_trans, chapter_num)
        out["transition_stage"] = stage
        out["transition_stage_label"] = STAGE_LABELS[stage]
        out["transition_stage_source"] = source
    return out
