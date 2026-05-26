"""
In-memory event bus — publish/subscribe for agent events.

Migrated from agents/events/event_bus.py.
"""
from __future__ import annotations

import asyncio
import logging
from collections import defaultdict, deque
from typing import Any, Callable, Coroutine

from framework.event_types import Event, AgentSuggestion

logger = logging.getLogger("inkoctobot.core.event_bus")

Listener = Callable[[Event], Coroutine[Any, Any, None] | None]


class EventBus:
    """In-memory publish/subscribe event bus."""

    def __init__(self) -> None:
        self._listeners: dict[str, list[Listener]] = defaultdict(list)
        self._global_listeners: list[Listener] = []
        self._suggestion_queue: asyncio.Queue[AgentSuggestion] = asyncio.Queue()
        self._max_history = 1000
        self._history: deque[Event] = deque(maxlen=self._max_history)

    def subscribe(self, event_type: str, listener: Listener) -> None:
        """Subscribe to a specific event type."""
        self._listeners[event_type].append(listener)

    def unsubscribe(self, event_type: str, listener: Listener) -> None:
        """Unsubscribe a listener from a specific event type."""
        listeners = self._listeners.get(event_type, [])
        try:
            listeners.remove(listener)
        except ValueError:
            pass

    def unsubscribe_all(self, listener: Listener) -> None:
        """Unsubscribe a global listener."""
        try:
            self._global_listeners.remove(listener)
        except ValueError:
            pass

    def subscribe_all(self, listener: Listener) -> None:
        """Subscribe to all events."""
        self._global_listeners.append(listener)

    def publish(self, event: Event) -> None:
        """Publish an event, notifying all relevant listeners."""
        self._history.append(event)

        listeners = list(self._listeners.get(event.event_type, []))
        listeners.extend(self._global_listeners)

        for listener in listeners:
            try:
                result = listener(event)
                if asyncio.iscoroutine(result):
                    task = asyncio.ensure_future(result)
                    task.add_done_callback(self._handle_task_error)
            except Exception as e:
                logger.error("Listener error for %s: %s", event.event_type, e)

    @staticmethod
    def _handle_task_error(task: asyncio.Task) -> None:
        """Log exceptions from fire-and-forget async listeners."""
        if task.cancelled():
            return
        exc = task.exception()
        if exc:
            logger.error("Async listener error: %s", exc)

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
        events: list[Event] | deque[Event] = self._history
        if event_type:
            events = [e for e in events if e.event_type == event_type]
        return [e.to_dict() for e in list(events)[-limit:]]

    def clear(self) -> None:
        self._listeners.clear()
        self._global_listeners.clear()
        self._history.clear()
