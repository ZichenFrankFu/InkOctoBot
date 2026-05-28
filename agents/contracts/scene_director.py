"""SceneDirector — schemas for ``SceneDirector.plan_scenes``.

The director takes a chapter outline + RAG context and returns a
structured scene plan. The LLM JSON schema lives in
``agents/production/scene_director.py::_build_planning_prompt`` —
these models mirror that JSON exactly so callers can validate the
output instead of duck-typing dicts.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


# ─────────── Request ───────────


class SceneDirectorRequest(BaseModel):
    """Inputs to ``SceneDirector.plan_scenes``."""
    model_config = ConfigDict(extra="ignore")

    chapter_outline: str
    chapter_num: int = 1
    memory_context: str = ""
    world_rules: str = ""
    character_cards: str = ""
    constraints: str = ""

    def to_legacy_kwargs(self) -> dict[str, Any]:
        """Spread to existing ``plan_scenes(**kwargs)`` signature."""
        return {
            "chapter_outline": self.chapter_outline,
            "chapter_num": self.chapter_num,
            "memory_context": self.memory_context,
            "world_rules": self.world_rules,
            "character_cards": self.character_cards,
            "constraints": self.constraints,
        }


# ─────────── Response sub-objects ───────────


class KnowledgeBoundary(BaseModel):
    """Per-character knowledge isolation rules for a scene."""
    model_config = ConfigDict(extra="allow")

    restricted_facts: list[str] = Field(default_factory=list)
    must_not_know: list[str] = Field(default_factory=list)


class CharacterInstruction(BaseModel):
    """Director's per-character brief for a single scene."""
    model_config = ConfigDict(extra="allow")

    emotional_state: str = ""
    secret_goal: str = ""
    knowledge_boundary: KnowledgeBoundary | None = None
    must: list[str] = Field(default_factory=list)
    must_not: list[str] = Field(default_factory=list)
    decision_options: list[Any] = Field(default_factory=list)


class ScenePlanItem(BaseModel):
    """One scene in the director's plan.

    Mirrors the JSON contract in ``scene_director._build_planning_prompt``.
    ``extra="allow"`` because LLMs sometimes add stray keys we want to
    preserve (e.g. ``beats_v2``, ``focus_character``).
    """
    model_config = ConfigDict(extra="allow")

    scene_index: int = 0
    location: str = ""
    time: str = ""
    characters: list[str] = Field(default_factory=list)
    summary: str = ""
    beats: list[str] = Field(default_factory=list)
    character_instructions: dict[str, CharacterInstruction] = Field(
        default_factory=dict,
    )
    narrator_instructions: str = ""


# ─────────── Response ───────────


class SceneDirectorResponse(BaseModel):
    """Output of ``SceneDirector.plan_scenes``.

    ``extra="allow"`` preserves the legacy diagnostic flags
    (``_retried``, ``_fallback``, ``_error``) emitted by the production
    director when the LLM fails and the fallback path kicks in.
    """
    model_config = ConfigDict(extra="allow")

    chapter_num: int | None = None
    scenes: list[ScenePlanItem] = Field(default_factory=list)
    chapter_arc: str = ""

    @classmethod
    def from_legacy_dict(cls, raw: dict[str, Any]) -> "SceneDirectorResponse":
        """Parse the dict the legacy ``plan_scenes`` returns.

        Falls back to a minimal valid object if ``raw`` is None or
        empty — callers shouldn't have to defend against malformed
        director output at this layer.
        """
        if not raw or not isinstance(raw, dict):
            return cls()
        return cls.model_validate(raw)

    @property
    def is_fallback(self) -> bool:
        """True when the director used its fallback single-scene plan."""
        extra = self.model_extra or {}
        return bool(extra.get("_fallback") or extra.get("_error"))
