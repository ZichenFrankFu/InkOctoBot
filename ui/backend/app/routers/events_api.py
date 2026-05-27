"""FastAPI router: Agent event stream websocket and history.

Resolves the bus via :func:`framework.global_event_bus.get_event_bus`
on every request so producers (post_commit pipeline notifications,
agent suggestions) and consumers (this router) share one instance
without an explicit ``set_event_bus`` call at startup.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query

from framework.global_event_bus import get_event_bus, set_event_bus as _set

router = APIRouter(prefix="/api/events", tags=["events"])
logger = logging.getLogger("inkoctobot.ui.backend.events_api")


def set_event_bus(bus: Any) -> None:
    """Back-compat shim — delegates to the global accessor.
    Kept so any external caller that imported it still works."""
    _set(bus)


@router.get("/health")
def health():
    return {"status": "ok", "router": "events"}


@router.get("/history")
def get_event_history(
    event_type: str | None = None,
    limit: int = Query(default=50, le=200),
):
    bus = get_event_bus()
    return {"events": bus.get_history(event_type=event_type, limit=limit)}


@router.get("/suggestions")
def get_pending_suggestions():
    bus = get_event_bus()
    suggestions = bus.get_suggestions_nowait()
    return {"suggestions": [s.to_dict() for s in suggestions]}


@router.websocket("/ws")
async def websocket_event_stream(websocket: WebSocket):
    """Long-lived WS feed of EventBus suggestions.

    Loops indefinitely: pulls suggestions from the bus, falls back to
    a heartbeat every 30 s of idleness so proxies / browsers don't
    close the connection. Exits cleanly on client disconnect.
    """
    await websocket.accept()
    bus = get_event_bus()
    try:
        while True:
            try:
                suggestion = await asyncio.wait_for(
                    bus.get_suggestion(), timeout=30.0,
                )
            except asyncio.TimeoutError:
                # Keepalive only — keep looping, don't exit.
                await websocket.send_json({"type": "heartbeat"})
                continue
            await websocket.send_json(suggestion.to_dict())
    except WebSocketDisconnect:
        logger.info("WebSocket client disconnected")
    except Exception as e:
        logger.error("WebSocket error: %s", e, exc_info=True)
        try:
            await websocket.close()
        except Exception:
            pass  # already closed
