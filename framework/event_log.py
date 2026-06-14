"""Persistent operation log for the EventBus (spec EventBus·机制3/机制4).

A global listener mirrors every published event into the
``operation_log`` table so user operations, agent operations, LLM
calls and DB changes survive restarts — the log viewer reads it, and
回溯模式 can rebuild history from it. The in-memory deque on EventBus
remains the hot path; this writer is strictly additive and must never
break publish() (every failure degrades to a debug log).
"""
from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from typing import Any

from framework.event_types import Event

logger = logging.getLogger("inkoctobot.framework.event_log")

_attached = False


def _db_path() -> str:
    from ui.backend.app.services.project_paths import get_db_path
    return get_db_path()


def persist_event(event: Event) -> None:
    """Synchronous single-row insert — events are user/agent-paced
    (not high frequency), so a plain insert keeps ordering simple."""
    try:
        data_json = json.dumps(event.data or {}, ensure_ascii=False,
                               default=str)[:4000]
        with sqlite3.connect(_db_path()) as con:
            con.execute(
                "INSERT INTO operation_log "
                "(log_id, event_type, source, project_id, data_json, event_ts) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (f"op_{uuid.uuid4().hex[:12]}", event.event_type,
                 event.source or "", event.project_id or "",
                 data_json, float(event.timestamp)),
            )
            con.commit()
    except Exception as e:
        logger.debug("operation_log write skipped: %s", e)


def attach_to_bus(bus: Any) -> None:
    """Idempotently register the persistence listener on a bus."""
    global _attached
    if _attached:
        return
    try:
        bus.subscribe_all(persist_event)
        _attached = True
    except Exception as e:
        logger.debug("event log attach skipped: %s", e)


def reset_for_tests() -> None:
    global _attached
    _attached = False


def query_log(
    db_path: str | None = None, *, project_id: str = "",
    event_type: str = "", limit: int = 100, offset: int = 0,
) -> list[dict[str, Any]]:
    """Read the persisted log, newest first (log viewer surface)."""
    where: list[str] = []
    args: list[Any] = []
    if project_id:
        where.append("project_id = ?")
        args.append(project_id)
    if event_type:
        where.append("event_type = ?")
        args.append(event_type)
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    try:
        with sqlite3.connect(db_path or _db_path()) as con:
            con.row_factory = sqlite3.Row
            rows = con.execute(
                f"SELECT log_id, event_type, source, project_id, data_json, "
                f"event_ts, created_at FROM operation_log {clause} "
                f"ORDER BY event_ts DESC LIMIT ? OFFSET ?",
                (*args, max(1, min(500, limit)), max(0, offset)),
            ).fetchall()
    except sqlite3.OperationalError:
        return []
    out: list[dict[str, Any]] = []
    for r in rows:
        d = dict(r)
        try:
            d["data"] = json.loads(d.pop("data_json") or "{}")
        except Exception:
            d["data"] = {}
        out.append(d)
    return out
