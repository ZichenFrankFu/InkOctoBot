"""
In-memory event bus — publish/subscribe for agent events.

Migrated from agents/events/event_bus.py.
"""
from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from typing import Any, Callable, Coroutine

from core.event_types import Event, AgentSuggestion

logger = logging.getLogger("inkoctobot.core.event_bus")

Listener = Callable[[Event], Coroutine[Any, Any, None] | None]


class EventBus:
    """In-memory publish/subscribe event bus."""

    def __init__(self) -> None:
        self._listeners: dict[str, list[Listener]] = defaultdict(list)
        self._global_listeners: list[Listener] = []
        self._suggestion_queue: asyncio.Queue[AgentSuggestion] = asyncio.Queue()
        self._history: list[Event] = []
        self._max_history = 1000

    def subscribe(self, event_type: str, listener: Listener) -> None:
        """Subscribe to a specific event type."""
        self._listeners[event_type].append(listener)

    def subscribe_all(self, listener: Listener) -> None:
        """Subscribe to all events."""
        self._global_listeners.append(listener)

    def publish(self, event: Event) -> None:
        """Publish an event, notifying all relevant listeners."""
        self._history.append(event)
        if len(self._history) > self._max_history:
            self._history = self._history[-self._max_history:]

        listeners = list(self._listeners.get(event.event_type, []))
        listeners.extend(self._global_listeners)

        for listener in listeners:
            try:
                result = listener(event)
                if asyncio.iscoroutine(result):
                    asyncio.ensure_future(result)
            except Exception as e:
                logger.error("Listener error for %s: %s", event.event_type, e)

    def push_suggestion(self, suggestion: AgentSuggestion) -> None:
        """Push a suggestion for the user (consumed by WebSocket)."""
        try:
            self._suggestion_queue.put_nowait(suggestion)
        except asyncio.QueueFull:
            logger.warning("Suggestion queue full, dropping: %s", suggestion.title)

    async def get_suggestion(self) -> AgentSuggestion:
        """Wait for the next suggestion (used by WebSocket endpoint)."""
        return await self._suggestion_queue.get()

    def get_suggestions_nowait(self) -> list[AgentSuggestion]:
        """Drain all pending suggestions without waiting."""
        suggestions: list[AgentSuggestion] = []
        while not self._suggestion_queue.empty():
            try:
                suggestions.append(self._suggestion_queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        return suggestions

    def get_history(
        self, event_type: str | None = None, limit: int = 50,
    ) -> list[dict[str, Any]]:
        events = self._history
        if event_type:
            events = [e for e in events if e.event_type == event_type]
        return [e.to_dict() for e in events[-limit:]]

    def clear(self) -> None:
        self._listeners.clear()
        self._global_listeners.clear()
        self._history.clear()
