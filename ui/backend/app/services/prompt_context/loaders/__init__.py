"""Prompt-context loaders.

One loader per spec block. Each module exposes ``plan(...)`` returning
a ``LoaderPlan`` (or ``None`` for inactive) and a backward-compatible
``load(...)`` thin wrapper. The builder runs ``plan`` for every loader,
runs the dynamic budget allocator, then calls each plan's ``render``.
"""
from . import (
    chapter_outline,
    character_cards,
    current_chapter_draft,
    foreshadowing,
    inspiration,
    platform_market,
    project_memory,
    reader_memory,
    reference,
    skills,
    storyland_state,
    subplots,
    user_preferences,
    user_special_requirements,
    worldbook,
)

__all__ = [
    "chapter_outline",
    "character_cards",
    "current_chapter_draft",
    "foreshadowing",
    "inspiration",
    "platform_market",
    "project_memory",
    "reader_memory",
    "reference",
    "skills",
    "storyland_state",
    "subplots",
    "user_preferences",
    "user_special_requirements",
    "worldbook",
]
