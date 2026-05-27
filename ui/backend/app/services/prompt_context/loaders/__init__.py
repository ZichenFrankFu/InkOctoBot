"""Per-RAG-block loaders.

Each module exposes a ``load(...)`` function returning either a
``\\n\\n## 标题\\n...`` string or ``""``. Failures degrade to ``""`` so a
broken loader can never block generation.
"""
from . import (
    adjacent_context,
    chapter_outline,
    character_cards,
    current_chapter_draft,
    foreshadowing,
    inspiration,
    platform_market,
    project_memory,
    reference,
    reference_blocks,
    skills,
    storyland_state,
    style_calibration,
    subplots,
    user_preferences,
    worldbook,
    writing_knowledge,
)

__all__ = [
    "adjacent_context",
    "chapter_outline",
    "character_cards",
    "current_chapter_draft",
    "foreshadowing",
    "inspiration",
    "platform_market",
    "project_memory",
    "reference",
    "reference_blocks",
    "skills",
    "storyland_state",
    "style_calibration",
    "subplots",
    "user_preferences",
    "worldbook",
    "writing_knowledge",
]
