"""
Scene Simulator — orchestrates multi-character interactions within a scene.

Coordinates Actor Agents and Narrator Agent in turn-based or parallel
mode, managing beat progression and information flow between actors.
"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from agents.production.actor_agent import ActorAgent
from agents.production.narrator_agent import NarratorAgent
from agents.production.writer import Writer
from framework.event_types import EventType

if TYPE_CHECKING:
    from agents.contracts import (
        SceneSimulatorRequest, SceneSimulatorResponse,
    )

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
        mode: str = "ensemble",
        scene_index: int = 0,
        decision_seed: int | str | None = None,
        character_params: dict[str, dict] | None = None,
    ) -> dict[str, Any]:
        """
        Simulate a complete scene.

        Default mode ``"ensemble"`` follows the spec (Actor·机制1-3):
        the code-level DecisionSampler pre-samples each character's
        behavior tendencies (seeded Bernoulli draws so users can replay
        a result via ``decision_seed``), and ALL on-stage characters are
        rendered in ONE Actor call. Legacy ``"turn_based"`` /
        ``"parallel"`` modes (one call per character) are kept for
        comparison and back-compat.

        Returns:
            {
                "performances": {character_name | "全体角色": text},
                "narrator": narrator_text,
                "combined": combined_performance_log,
                "behavior_directives": {character_name: directive_text},
                "decision_seeds": {character_name: int},
            }
        """
        characters = scene_plan.get("characters", [])

        # ── 决策采样器（代码采样，零 LLM；Actor·机制1） ──
        directives: dict[str, str] = {}
        seeds: dict[str, int] = {}
        try:
            from knowledge.decision_engine import DecisionSampler
            sampler = DecisionSampler(
                project_id=self.project_id,
                chapter_num=chapter_num,
                scene_index=scene_index,
                base_seed=decision_seed,
            )
            for name, d in sampler.sample_scene(
                characters, character_params or {},
            ).items():
                directives[name] = d.to_text()
                seeds[name] = d.seed
        except Exception as ds_err:
            logger.debug("DecisionSampler skipped: %s", ds_err)

        # ── 每角色知识视图（知识隔离） ──
        knowledge_views: dict[str, str] = {}
        for char_name in characters:
            try:
                knowledge_views[char_name] = self.memory.get_context_for_actor(
                    char_name, chapter_num,
                    scene_context=scene_plan.get("summary", ""),
                    knowledge_boundary=scene_plan.get(
                        "character_instructions", {},
                    ).get(char_name, {}).get("knowledge_boundary"),
                )
            except Exception as kv_err:
                logger.debug("knowledge view skipped for %s: %s",
                             char_name, kv_err)
                knowledge_views[char_name] = ""

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
        if mode == "ensemble":
            ensemble_actor = ActorAgent(
                self.router,
                character_name="全体角色",
                project_id=self.project_id,
                event_bus=self.event_bus,
            )
            ensemble_text = await ensemble_actor.perform_scene(
                scene_plan, character_cards,
                behavior_directives=directives,
                knowledge_views=knowledge_views,
                previous_scene_text=self.memory.immediate.get_context_text(),
                constraints=constraints,
            )
            performances = {"全体角色": ensemble_text}
        else:
            actors = self._build_individual_actors(
                characters, character_cards, knowledge_views, directives,
            )
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
            "behavior_directives": directives,
            "decision_seeds": seeds,
        }

    def _build_individual_actors(
        self,
        characters: list[str],
        character_cards: dict[str, str],
        knowledge_views: dict[str, str],
        directives: dict[str, str],
    ) -> dict[str, ActorAgent]:
        """Legacy per-character actor instances (turn_based / parallel)."""
        actors: dict[str, ActorAgent] = {}
        for char_name in characters:
            extra_context = knowledge_views.get(char_name, "")
            directive = directives.get(char_name, "")
            if directive:
                extra_context += f"\n\n[行为指令]\n{directive}"
            actors[char_name] = ActorAgent(
                self.router,
                character_name=char_name,
                character_card=character_cards.get(char_name, ""),
                project_id=self.project_id,
                event_bus=self.event_bus,
                extra_system=extra_context,
            )
        return actors

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

    async def simulate_chapter_typed(
        self, request: "SceneSimulatorRequest",
    ) -> "SceneSimulatorResponse":
        """Stage 4 typed wrapper around ``simulate_chapter``."""
        from agents.contracts import SceneSimulatorResponse
        kwargs = request.to_legacy_kwargs()
        # legacy method takes its mode arg only via simulate_scene
        kwargs.pop("mode", None)
        raw = await self.simulate_chapter(**kwargs)
        return SceneSimulatorResponse.from_legacy_list(raw)

    async def simulate_chapter(
        self,
        scene_plans: list[dict[str, Any]],
        character_cards: dict[str, str],
        *,
        chapter_num: int = 1,
        style_profile: str = "",
        constraints: str = "",
        mode: str = "ensemble",
        decision_seed: int | str | None = None,
        character_params: dict[str, dict] | None = None,
    ) -> list[dict[str, Any]]:
        """Simulate all scenes in a chapter sequentially."""
        results = []
        for i, scene_plan in enumerate(scene_plans):
            from knowledge.reader_memory.immediate import SceneContext
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
                mode=mode,
                scene_index=i,
                decision_seed=decision_seed,
                character_params=character_params,
            )
            self.memory.update_scene_text(result["combined"])
            results.append(result)
            logger.info("Scene %d/%d complete", i + 1, len(scene_plans))

        return results
