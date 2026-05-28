"""Evaluator — schemas for ``Evaluator.evaluate_chapter``.

The evaluator returns a rich dict with score, passed, issues,
dimension breakdown, summary text, and a process log. These models
mirror the dict ``Evaluator._enrich_evaluation`` produces.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


# ─────────── Request ───────────


class EvaluatorRequest(BaseModel):
    """Inputs to ``Evaluator.evaluate_chapter``."""
    model_config = ConfigDict(extra="ignore")

    chapter_text: str
    chapter_num: int = 1
    scene_plan: dict[str, Any] | None = None
    character_cards: str = ""
    memory_context: str = ""
    constraints: str = ""
    max_retries: int = 3

    def to_legacy_kwargs(self) -> dict[str, Any]:
        return {
            "chapter_text": self.chapter_text,
            "chapter_num": self.chapter_num,
            "scene_plan": self.scene_plan,
            "character_cards": self.character_cards,
            "memory_context": self.memory_context,
            "constraints": self.constraints,
            "max_retries": self.max_retries,
        }


# ─────────── Response sub-objects ───────────


class EvaluationIssue(BaseModel):
    """Single problem the evaluator flagged."""
    model_config = ConfigDict(extra="allow")

    type: str = ""
    dimension: str = ""
    severity: str = "low"  # high / medium / low
    message: str = ""


class DimensionScores(BaseModel):
    """Per-axis scoring snapshot produced by ``_enrich_evaluation``.

    All scores in 0-100. Defaults match the baseline the enricher
    starts from before applying issue penalties.
    """
    model_config = ConfigDict(extra="allow")

    constraint_satisfaction: int = 85
    consistency: int = 85
    knowledge_isolation: int = 90
    repetition: int = 85
    ai_flavor: int = 80
    narrative_quality: int = 80


# ─────────── Response ───────────


class EvaluatorResponse(BaseModel):
    """Output of ``Evaluator.evaluate_chapter``.

    ``extra="allow"`` preserves keys the legacy enricher writes that
    haven't been promoted to structured fields yet (raw_evaluation,
    custom analyzer outputs, etc.).
    """
    model_config = ConfigDict(extra="allow")

    passed: bool = True
    score: int = 0
    issues: list[EvaluationIssue] = Field(default_factory=list)
    strengths: list[str] = Field(default_factory=list)
    dimension_scores: DimensionScores = Field(default_factory=DimensionScores)
    summary_text: str = ""
    process_log: list[dict[str, Any]] = Field(default_factory=list)

    @classmethod
    def from_legacy_dict(cls, raw: dict[str, Any] | None) -> "EvaluatorResponse":
        """Tolerant parse — accepts the dict produced by
        ``_enrich_evaluation`` even when its issues list is loosely
        typed."""
        if not raw or not isinstance(raw, dict):
            return cls()
        return cls.model_validate(raw)

    @property
    def high_severity_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == "high")

    def to_legacy_dict(self) -> dict[str, Any]:
        """Round-trip back to the dict shape the existing pipeline
        passes to the WebSocket emit + ChapterContext.commit."""
        return self.model_dump()
