"""Process-global :class:`EventBus` singleton.

The EventBus has no natural owner — agents publish, routers consume,
notification service double-writes. Having a single accessor lets each
side import the same instance without a circular dep on a specific
router. Tests can override via :func:`set_event_bus`.
"""
from __future__ import annotations

import threading

from framework.event_bus import EventBus


_bus: EventBus | None = None
_lock = threading.Lock()


def get_event_bus() -> EventBus:
    """Return the process-global EventBus, creating it on first call.

    The persistent operation-log listener (spec EventBus·机制3) is
    attached on first creation so every published event is mirrored
    into the ``operation_log`` table for the log viewer + 回溯模式."""
    global _bus
    if _bus is None:
        with _lock:
            if _bus is None:
                _bus = EventBus()
                try:
                    from framework.event_log import attach_to_bus
                    attach_to_bus(_bus)
                except Exception:
                    pass
    return _bus


def set_event_bus(bus: EventBus | None) -> None:
    """Swap the singleton — used by tests to inject a fresh bus."""
    global _bus
    with _lock:
        _bus = bus


def reset_for_tests() -> None:
    """Drop the singleton; next ``get_event_bus()`` builds a fresh one."""
    set_event_bus(None)
