"""Per-RAG-block loaders.

Each module exposes a ``load(...)`` function returning either a
``\\n\\n## 标题\\n...`` string or ``""``. Failures degrade to ``""`` so a
broken loader can never block generation.
"""
from . import (
    adjacent_context,
    character_cards,
    foreshadowing,
    platform_market,
    project_memory,
    reference_blocks,
    style_calibration,
    user_preferences,
    worldbook,
    writing_knowledge,
)

__all__ = [
    "adjacent_context",
    "character_cards",
    "foreshadowing",
    "platform_market",
    "project_memory",
    "reference_blocks",
    "style_calibration",
    "user_preferences",
    "worldbook",
    "writing_knowledge",
]
