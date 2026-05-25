"""
Agent trigger registry — condition-based proactive intervention.

Migrated from agents/events/triggers.py.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine

from framework.event_types import Event, AgentSuggestion
from framework.event_bus import EventBus

logger = logging.getLogger("inkoctobot.core.triggers")


@dataclass
class TriggerRule:
    """A single trigger rule for an agent."""
    agent_name: str
    event_types: list[str]
    condition: Callable[[Event], bool]
    action: Callable[[Event], Coroutine[Any, Any, AgentSuggestion | None]]
    cooldown_chapters: int = 1
    is_enabled: bool = True
    _last_fired_chapter: int = field(default=0, repr=False)

    def can_fire(self, current_chapter: int) -> bool:
        if not self.is_enabled:
            return False
        return (current_chapter - self._last_fired_chapter) >= self.cooldown_chapters

    def mark_fired(self, chapter: int) -> None:
        self._last_fired_chapter = chapter


class TriggerRegistry:
    """Manages trigger rules for all agents."""

    def __init__(self, event_bus: EventBus):
        self.event_bus = event_bus
        self._triggers: list[TriggerRule] = []
        self._current_chapter = 0
        event_bus.subscribe_all(self._on_event)

    def register(self, trigger: TriggerRule) -> None:
        self._triggers.append(trigger)
        logger.info(
            "Registered trigger: %s on %s",
            trigger.agent_name, trigger.event_types,
        )

    def set_chapter(self, chapter_num: int) -> None:
        self._current_chapter = chapter_num

    def _on_event(self, event: Event) -> None:
        """Check all triggers against the incoming event."""
        for trigger in self._triggers:
            if event.event_type not in trigger.event_types:
                continue
            if not trigger.can_fire(self._current_chapter):
                continue
            try:
                if trigger.condition(event):
                    coro = trigger.action(event)
                    if coro is not None:
                        asyncio.ensure_future(self._execute_trigger(trigger, coro))
            except Exception as e:
                logger.error("Trigger condition error (%s): %s", trigger.agent_name, e)

    async def _execute_trigger(
        self,
        trigger: TriggerRule,
        coro: Coroutine[Any, Any, AgentSuggestion | None],
    ) -> None:
        try:
            suggestion = await coro
            if suggestion:
                self.event_bus.push_suggestion(suggestion)
                trigger.mark_fired(self._current_chapter)
                logger.info("Trigger fired: %s", trigger.agent_name)
        except Exception as e:
            logger.error("Trigger action error (%s): %s", trigger.agent_name, e)

    def list_triggers(self) -> list[dict[str, Any]]:
        return [
            {
                "agent": t.agent_name,
                "events": t.event_types,
                "enabled": t.is_enabled,
                "cooldown": t.cooldown_chapters,
                "last_fired": t._last_fired_chapter,
            }
            for t in self._triggers
        ]

    def set_enabled(self, agent_name: str, enabled: bool) -> None:
        for t in self._triggers:
            if t.agent_name == agent_name:
                t.is_enabled = enabled
