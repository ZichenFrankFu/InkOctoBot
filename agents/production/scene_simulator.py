"""
Scene Simulator — orchestrates multi-character interactions within a scene.

Coordinates Actor Agents and Narrator Agent in turn-based or parallel
mode, managing beat progression and information flow between actors.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from agents.production.actor_agent import ActorAgent
from agents.production.narrator_agent import NarratorAgent
from agents.production.editor_writer import EditorWriter
from agents.events.event_types import EventType

logger = logging.getLogger("inkoctobot.agents.production.scene_simulator")


class SceneSimulator:
    """Orchestrates multi-actor scene simulation."""

    def __init__(
        self,
        router: Any,
        memory_manager: Any,
        *,
        project_id: str = "",
        event_bus: Any | None = None,
    ):
        self.router = router
        self.memory = memory_manager
        self.project_id = project_id
        self.event_bus = event_bus
        self._narrator = NarratorAgent(
            router, project_id=project_id, event_bus=event_bus,
        )

    async def simulate_scene(
        self,
        scene_plan: dict[str, Any],
        character_cards: dict[str, str],
        *,
        chapter_num: int = 1,
        style_profile: str = "",
        constraints: str = "",
        mode: str = "turn_based",
    ) -> dict[str, Any]:
        """
        Simulate a complete scene.

        Returns:
            {
                "performances": {character_name: performance_text},
                "narrator": narrator_text,
                "combined": combined_performance_log,
            }
        """
        characters = scene_plan.get("characters", [])

        # Create actor instances with knowledge isolation
        actors: dict[str, ActorAgent] = {}
        for char_name in characters:
            knowledge_view = self.memory.get_context_for_actor(
                char_name, chapter_num,
                scene_context=scene_plan.get("summary", ""),
                knowledge_boundary=scene_plan.get("character_instructions", {}).get(
                    char_name, {},
                ).get("knowledge_boundary"),
            )
            actors[char_name] = ActorAgent(
                self.router,
                character_name=char_name,
                character_card=character_cards.get(char_name, ""),
                project_id=self.project_id,
                event_bus=self.event_bus,
                extra_system=knowledge_view,
            )

        # Generate narrator text
        narrator_instructions = scene_plan.get("narrator_instructions", "")
        narrator_text = await self._narrator.narrate(
            scene_plan,
            narrator_instructions=narrator_instructions,
            scene_context=self.memory.immediate.get_context_text(),
            style_profile=style_profile,
            constraints=constraints,
        )

        # Generate actor performances
        if mode == "parallel":
            performances = await self._simulate_parallel(
                actors, scene_plan, constraints,
            )
        else:
            performances = await self._simulate_turn_based(
                actors, scene_plan, constraints,
            )

        # Combine into a single performance log
        combined = self._combine_performances(performances, narrator_text, scene_plan)

        return {
            "performances": performances,
            "narrator": narrator_text,
            "combined": combined,
        }

    async def _simulate_turn_based(
        self, actors: dict[str, ActorAgent],
        scene_plan: dict, constraints: str,
    ) -> dict[str, str]:
        """Turn-based: actors take turns in order."""
        performances: dict[str, str] = {}
        accumulated = ""

        for char_name, actor in actors.items():
            perf = await actor.perform(
                scene_plan,
                previous_beats=accumulated,
                constraints=constraints,
            )
            performances[char_name] = perf
            accumulated += f"\n[{char_name}的表演]\n{perf}\n"

        return performances

    async def _simulate_parallel(
        self, actors: dict[str, ActorAgent],
        scene_plan: dict, constraints: str,
    ) -> dict[str, str]:
        """Parallel: all actors perform simultaneously."""
        async def _perform(name: str, actor: ActorAgent) -> tuple[str, str]:
            perf = await actor.perform(scene_plan, constraints=constraints)
            return name, perf

        tasks = [_perform(n, a) for n, a in actors.items()]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        performances: dict[str, str] = {}
        for result in results:
            if isinstance(result, Exception):
                logger.error("Actor error: %s", result)
                continue
            name, perf = result
            performances[name] = perf

        return performances

    @staticmethod
    def _combine_performances(
        performances: dict[str, str], narrator: str, scene_plan: dict,
    ) -> str:
        """Merge all performances and narration into a single log."""
        parts = [f"=== 场景: {scene_plan.get('summary', '')} ===\n"]
        if narrator:
            parts.append(f"[旁白]\n{narrator}\n")
        for char_name, perf in performances.items():
            parts.append(f"[{char_name}的表演]\n{perf}\n")
        return "\n".join(parts)

    async def simulate_chapter(
        self,
        scene_plans: list[dict[str, Any]],
        character_cards: dict[str, str],
        *,
        chapter_num: int = 1,
        style_profile: str = "",
        constraints: str = "",
    ) -> list[dict[str, Any]]:
        """Simulate all scenes in a chapter sequentially."""
        results = []
        for i, scene_plan in enumerate(scene_plans):
            from rag.memory.immediate import SceneContext
            self.memory.start_scene(SceneContext(
                scene_index=i,
                characters=scene_plan.get("characters", []),
                location=scene_plan.get("location", ""),
                time_marker=scene_plan.get("time", ""),
            ))
            result = await self.simulate_scene(
                scene_plan, character_cards,
                chapter_num=chapter_num,
                style_profile=style_profile,
                constraints=constraints,
            )
            self.memory.update_scene_text(result["combined"])
            results.append(result)
            logger.info("Scene %d/%d complete", i + 1, len(scene_plans))

        return results
