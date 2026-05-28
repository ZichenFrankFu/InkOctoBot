"""SceneSimulator — schemas for ``SceneSimulator.simulate_chapter``.

The simulator runs ActorAgent + NarratorAgent per scene. Output is a
list of per-scene results.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .scene_director import ScenePlanItem


# ─────────── Request ───────────


class SceneSimulatorRequest(BaseModel):
    """Inputs to ``SceneSimulator.simulate_chapter``."""
    model_config = ConfigDict(extra="ignore", arbitrary_types_allowed=True)

    scene_plans: list[ScenePlanItem | dict[str, Any]] = Field(default_factory=list)
    character_cards: dict[str, str] = Field(default_factory=dict)
    chapter_num: int = 1
    style_profile: str = ""
    constraints: str = ""
    mode: str = "turn_based"

    def to_legacy_kwargs(self) -> dict[str, Any]:
        """Spread to ``simulate_chapter(**kwargs)``.

        Scene plans are downgraded to plain dicts because the legacy
        simulator dict-indexes them (e.g. ``scene_plan.get("characters")``).
        """
        plans: list[dict[str, Any]] = []
        for p in self.scene_plans:
            if isinstance(p, ScenePlanItem):
                plans.append(p.model_dump(by_alias=True))
            else:
                plans.append(p)
        return {
            "scene_plans": plans,
            "character_cards": self.character_cards,
            "chapter_num": self.chapter_num,
            "style_profile": self.style_profile,
            "constraints": self.constraints,
        }


# ─────────── Per-scene response ───────────


class SceneSimulationResult(BaseModel):
    """Output of ``simulate_scene`` — one entry per scene plan."""
    model_config = ConfigDict(extra="allow")

    performances: dict[str, str] = Field(default_factory=dict)
    narrator: str = ""
    combined: str = ""


# ─────────── Whole-chapter response ───────────


class SceneSimulatorResponse(BaseModel):
    """Output of ``SceneSimulator.simulate_chapter``.

    The legacy method returns ``list[dict]`` directly; this wrapper
    boxes it so observability tables can store one row per simulator
    run with structured fields.
    """
    scene_results: list[SceneSimulationResult] = Field(default_factory=list)

    @classmethod
    def from_legacy_list(
        cls, raw: list[dict[str, Any]] | None,
    ) -> "SceneSimulatorResponse":
        if not raw:
            return cls()
        return cls(scene_results=[
            SceneSimulationResult.model_validate(r) for r in raw
        ])

    def to_legacy_list(self) -> list[dict[str, Any]]:
        return [r.model_dump() for r in self.scene_results]

    def combined_performance_records(self) -> list[str]:
        """Flatten all per-actor performances into a list — the shape
        ``Writer.assemble_chapter`` wants."""
        out: list[str] = []
        for scene in self.scene_results:
            for actor, text in scene.performances.items():
                out.append(f"[{actor}]\n{text}")
        return out

    def combined_narrator_text(self) -> str:
        """Concatenate every scene's narrator output — the shape
        ``Writer.assemble_chapter`` wants."""
        return "\n\n".join(s.narrator for s in self.scene_results if s.narrator)
