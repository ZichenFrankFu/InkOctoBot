"""FastAPI router: Agent event stream websocket and history."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query

router = APIRouter(prefix="/api/events", tags=["events"])
logger = logging.getLogger("inkoctobot.ui.backend.events_api")

_event_bus = None


def set_event_bus(bus: Any) -> None:
    global _event_bus
    _event_bus = bus


@router.get("/health")
def health():
    return {"status": "ok", "router": "events"}


@router.get("/history")
def get_event_history(
    event_type: str | None = None,
    limit: int = Query(default=50, le=200),
):
    if _event_bus is None:
        return {"events": [], "note": "Event bus not initialized"}
    return {"events": _event_bus.get_history(event_type=event_type, limit=limit)}


@router.get("/suggestions")
def get_pending_suggestions():
    if _event_bus is None:
        return {"suggestions": []}
    suggestions = _event_bus.get_suggestions_nowait()
    return {"suggestions": [s.to_dict() for s in suggestions]}


@router.websocket("/ws")
async def websocket_event_stream(websocket: WebSocket):
    """Long-lived WS feed of EventBus suggestions.

    Loops indefinitely: pulls suggestions from the bus, falls back to
    a heartbeat every 30 s of idleness so proxies / browsers don't
    close the connection. Exits cleanly on client disconnect.
    """
    await websocket.accept()
    if _event_bus is None:
        await websocket.send_json({"error": "Event bus not initialized"})
        await websocket.close()
        return
    try:
        while True:
            try:
                suggestion = await asyncio.wait_for(
                    _event_bus.get_suggestion(), timeout=30.0,
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
