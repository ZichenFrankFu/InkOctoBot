"""Developer observability endpoints.

These are gated behind ``--test`` mode or ``INKOCTO_DEBUG=1``. They
expose the in-memory log buffer, recent EventBus events, per-session
trace logs, provider/DB diagnostics and the live usage snapshot —
enough for a developer to answer "what is the app doing right now?"
without restarting it.

Mounted at ``/api/debug`` by ``ui.backend.app.main`` (no extra prefix
since this router declares its own).
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException

logger = logging.getLogger("inkoctobot.ui.backend.debug_api")

router = APIRouter(prefix="/api/debug", tags=["debug"])


def _debug_enabled() -> bool:
    """Debug endpoints are on in test mode or with INKOCTO_DEBUG=1."""
    if os.environ.get("WN_TEST_MODE") == "1":
        return True
    return bool(os.environ.get("INKOCTO_DEBUG", "").strip())


def _require_debug() -> None:
    if not _debug_enabled():
        raise HTTPException(
            403,
            "Debug endpoints disabled. Set INKOCTO_DEBUG=1 or run with --test.",
        )


@router.get("/status")
def status() -> dict[str, Any]:
    """Quick liveness probe — works even when debug is off."""
    from ui.backend.app.settings import settings as _s
    return {
        "ok": True,
        "debug_enabled": _debug_enabled(),
        "test_mode": _s.test_mode,
    }


@router.get("/recent-logs")
def recent_logs(
    limit: int = 50,
    level: str | None = None,
    logger_prefix: str | None = None,
    trace_id: str | None = None,
    session_id: str | None = None,
) -> dict[str, Any]:
    """Return the most recent log records from the in-memory ring buffer.

    Filters: ``level`` (e.g. ``INFO``), ``logger_prefix`` (e.g.
    ``inkoctobot.agents``), ``trace_id``, ``session_id``.
    """
    _require_debug()
    from framework.observability import get_buffer

    records = get_buffer().recent(
        limit=limit,
        level=level,
        logger_prefix=logger_prefix,
        trace_id=trace_id,
        session_id=session_id,
    )
    return {"count": len(records), "records": records}


@router.get("/trace/{trace_id}")
def trace_logs(trace_id: str, limit: int = 500) -> dict[str, Any]:
    """All in-memory log records that carry the given trace_id."""
    _require_debug()
    from framework.observability import get_buffer

    records = get_buffer().recent(limit=limit, trace_id=trace_id)
    return {"trace_id": trace_id, "count": len(records), "records": records}


@router.get("/session/{session_id}")
def session_logs(session_id: str, limit: int = 500) -> dict[str, Any]:
    """All in-memory log records that carry the given session_id."""
    _require_debug()
    from framework.observability import get_buffer

    records = get_buffer().recent(limit=limit, session_id=session_id)
    return {"session_id": session_id, "count": len(records), "records": records}


@router.get("/event-bus")
def event_bus_history(event_type: str | None = None, limit: int = 100) -> dict[str, Any]:
    """Return recent EventBus history, optionally filtered by event type."""
    _require_debug()
    try:
        from framework.event_bus import EventBus
        bus = EventBus.instance() if hasattr(EventBus, "instance") else None
        if bus is None:
            return {"count": 0, "events": [],
                    "note": "no active EventBus singleton"}
        events = bus.get_history(event_type=event_type, limit=limit)
        return {"count": len(events), "events": [
            {"type": e.type, "ts": getattr(e, "timestamp", None),
             "payload": getattr(e, "payload", {})}
            for e in events
        ]}
    except Exception as e:
        return {"count": 0, "events": [], "error": str(e)}


@router.get("/usage")
def usage_snapshot() -> dict[str, Any]:
    """Live usage tracker snapshot (mirrors /api/generation/usage)."""
    _require_debug()
    from ui.backend.app.services import usage_tracker
    return usage_tracker.snapshot()


@router.get("/diagnostics")
def diagnostics() -> dict[str, Any]:
    """One-shot health summary: DB sizes, log buffer stats, active sessions.

    Designed as the "show me everything that matters" endpoint for
    support: the user pastes its output, you read it.
    """
    _require_debug()
    from ui.backend.app.settings import settings as _s
    out: dict[str, Any] = {
        "repo_root": str(_s.repo_root),
        "data_dir": str(_s.data_dir) if _s.data_dir else None,
        "test_mode": _s.test_mode,
        "databases": {},
        "log_buffer": {},
    }

    base = _s.data_dir if _s.data_dir else _s.repo_root / "data"
    for name in ("novels.db", "InkOctoBot_Crawler.db", "references.db"):
        p = Path(base) / name
        out["databases"][name] = {
            "exists": p.exists(),
            "bytes": p.stat().st_size if p.exists() else 0,
        }

    try:
        from framework.observability import get_buffer
        recs = get_buffer().recent(limit=10000)
        out["log_buffer"] = {
            "total_records": len(recs),
            "by_level": {
                lv: sum(1 for r in recs if r["level"] == lv)
                for lv in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
            },
        }
    except Exception as e:
        out["log_buffer"] = {"error": str(e)}

    try:
        from ui.backend.app.routers import generation_api
        active = getattr(generation_api, "_active_sessions", {})
        out["active_generation_sessions"] = len(active)
    except Exception:
        out["active_generation_sessions"] = -1

    return out
