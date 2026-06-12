"""Learning subsystem — batch-extracted writing preferences (Part A) +
domain knowledge compilation (Part B).

Single entry point most callers should use:

    from agents.learning import maybe_capture_and_fire

    maybe_capture_and_fire(
        db_path=..., project_id=..., chapter_id=...,
        chapter_num=..., source="user_edit", edited_text=...,
    )

That helper handles the source-gate (only ``user_edit`` is learned-from),
the baseline lookup, the cheap diff stats, the row insert, and the
fire-and-forget batch extraction when the threshold is reached. All
work is best-effort — a learning hiccup never breaks the commit path.
"""
from __future__ import annotations

from .edit_observations import (
    capture_observation,
    count_unconsumed,
    maybe_capture_and_fire,
)
from .edit_batch_extractor import (
    extract_batch,
    fire_batch_extraction,
)

__all__ = [
    "capture_observation",
    "count_unconsumed",
    "maybe_capture_and_fire",
    "extract_batch",
    "fire_batch_extraction",
]
