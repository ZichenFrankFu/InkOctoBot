"""Writer — schemas for ``Writer.assemble_chapter`` + ``Writer.write_chapter``.

Both modes return raw prose string. The Response wraps it so callers
can carry metadata (length, mode, chapter_num) alongside the text in
observability rows.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


# ─────────── Assembly-mode request ───────────


class WriterAssembleRequest(BaseModel):
    """Inputs to ``Writer.assemble_chapter`` — multi-agent final stage."""
    model_config = ConfigDict(extra="ignore")

    performance_records: list[str] = Field(default_factory=list)
    narrator_text: str = ""
    chapter_num: int = 1
    chapter_title: str = ""
    narrative_instructions: str = ""
    style_profile: str = ""
    user_preferences: str = ""
    memory_context: str = ""
    constraints: str = ""
    truth_bundle: str = ""

    def to_legacy_kwargs(self) -> dict[str, Any]:
        return self.model_dump()


# ─────────── Single-agent-mode request ───────────


class WriterWriteRequest(BaseModel):
    """Inputs to ``Writer.write_chapter`` — single-agent fast path."""
    model_config = ConfigDict(extra="ignore")

    outline: str
    chapter_num: int = 1
    chapter_title: str = ""
    synopsis: str = ""
    time_label: str = ""
    location: str = ""
    characters: list[str] = Field(default_factory=list)
    pov_character: str = ""
    character_cards: str = ""
    world_rules: str = ""
    narrative_instructions: str = ""
    style_profile: str = ""
    user_preferences: str = ""
    memory_context: str = ""
    adjacent_context: str = ""
    constraints: str = ""
    target_word_count: int = 2000
    truth_bundle: str = ""
    pressured_hooks_text: str = ""
    ledger_anchors_text: str = ""

    def to_legacy_kwargs(self) -> dict[str, Any]:
        return self.model_dump()


# ─────────── Response (shared) ───────────


class WriterResponse(BaseModel):
    """Output of both ``assemble_chapter`` and ``write_chapter``."""
    model_config = ConfigDict(extra="allow")

    chapter_text: str = ""
    chapter_num: int = 1
    mode: Literal["assembly", "single_writer"] = "assembly"

    @classmethod
    def from_text(
        cls, text: str, *, chapter_num: int = 1,
        mode: Literal["assembly", "single_writer"] = "assembly",
    ) -> "WriterResponse":
        return cls(chapter_text=text or "", chapter_num=chapter_num, mode=mode)

    @property
    def char_count(self) -> int:
        return len(self.chapter_text)
