"""Pipeline session persistence — read/write helpers for
``pipeline_sessions``.

Stage 5: replace the previously-volatile in-memory ``_active_sessions``
with a hybrid in-memory + DB-backed store. The in-memory dict stays
authoritative for **running** sessions (it owns the asyncio.Task /
Event handles), and this module mirrors the persistable subset to DB
on every state transition. The DB then survives server restarts,
giving us:

  * a history view of past sessions (which chapters were tried, which
    failed, who paused)
  * a recovery signal — running sessions whose process was killed get
    re-tagged as ``'interrupted'`` on the next boot
  * a structured audit trail for Stage 6 observability

All write helpers are **best-effort**: a DB write failure logs the
error but doesn't propagate — pipeline correctness can't depend on
persistence. The in-memory dict is still the source of truth at
runtime.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import time
from typing import Any

logger = logging.getLogger("inkoctobot.services.pipeline_session_store")


# ─── Fields that are safe to persist (everything except runtime handles) ───
# The session dict in generation_api.py also stores ``task`` (asyncio.Task),
# ``confirm_event`` (asyncio.Event), ``manual_event`` (asyncio.Event), and
# ``chapter_context`` (live pydantic object). We drop all four when
# mirroring — they can't be JSON-serialized and they don't survive a
# process boundary anyway.

_PERSISTABLE_KEYS: frozenset[str] = frozenset({
    "status", "current_step", "trace_id",
    "request", "events", "result", "text",
    "waiting_confirm", "waiting_manual", "manual_step",
    "created_at",
    # error_message is set separately when status='error'
})


def _conn(db_path: str) -> sqlite3.Connection:
    c = sqlite3.connect(db_path)
    c.row_factory = sqlite3.Row
    return c


def _safe_json_dumps(value: Any) -> str:
    """Serialize to JSON, returning '' on failure. asyncio.Task / Event
    raise TypeError → we want best-effort mirroring, not exception."""
    if value is None:
        return ""
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except Exception:
        return ""


# ─────────── Write path ───────────


def upsert_session(
    db_path: str,
    session_id: str,
    session_dict: dict[str, Any],
    *,
    project_id: str = "",
    chapter_id: str = "",
    chapter_num: int = 0,
    error_message: str = "",
) -> None:
    """Mirror the persistable subset of ``session_dict`` to DB.

    Called from generation_api.py write sites — never raises;
    swallows DB errors with a debug log so the pipeline keeps
    running even when the DB hiccups.

    The ``project_id`` / ``chapter_id`` / ``chapter_num`` args are
    pulled from the GenerateRequest at session-create time and reused
    on every subsequent upsert (the session dict's ``request`` carries
    them but parsing it on every write is wasteful).
    """
    if not db_path or not session_id:
        return

    now = time.time()
    created_at = float(session_dict.get("created_at") or now)
    status = str(session_dict.get("status") or "pending")
    current_step = str(session_dict.get("current_step") or "")
    trace_id = str(session_dict.get("trace_id") or "")
    request = session_dict.get("request") or {}
    events = session_dict.get("events") or []
    result = session_dict.get("result")
    text = str(session_dict.get("text") or "")
    waiting_confirm = 1 if session_dict.get("waiting_confirm") else 0
    waiting_manual = 1 if session_dict.get("waiting_manual") else 0
    manual_step = str(session_dict.get("manual_step") or "")
    completed_at: float | None = None
    if status in ("complete", "error", "stopped", "interrupted"):
        completed_at = now

    try:
        with _conn(db_path) as c:
            c.execute(
                """
                INSERT INTO pipeline_sessions
                    (session_id, project_id, chapter_id, chapter_num,
                     status, current_step, trace_id,
                     request_json, events_json, result_json, text,
                     waiting_confirm, waiting_manual, manual_step,
                     error_message, created_at, updated_at, completed_at)
                VALUES (?, ?, ?, ?,  ?, ?, ?,  ?, ?, ?, ?,
                        ?, ?, ?,  ?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    project_id       = excluded.project_id,
                    chapter_id       = excluded.chapter_id,
                    chapter_num      = excluded.chapter_num,
                    status           = excluded.status,
                    current_step     = excluded.current_step,
                    trace_id         = excluded.trace_id,
                    request_json     = excluded.request_json,
                    events_json      = excluded.events_json,
                    result_json      = excluded.result_json,
                    text             = excluded.text,
                    waiting_confirm  = excluded.waiting_confirm,
                    waiting_manual   = excluded.waiting_manual,
                    manual_step      = excluded.manual_step,
                    error_message    = COALESCE(NULLIF(excluded.error_message, ''),
                                                 pipeline_sessions.error_message),
                    updated_at       = excluded.updated_at,
                    completed_at     = COALESCE(excluded.completed_at,
                                                 pipeline_sessions.completed_at)
                """,
                (
                    session_id, project_id, chapter_id, chapter_num,
                    status, current_step, trace_id,
                    _safe_json_dumps(request),
                    _safe_json_dumps(events),
                    _safe_json_dumps(result),
                    text,
                    waiting_confirm, waiting_manual, manual_step,
                    error_message, created_at, now, completed_at,
                ),
            )
    except Exception as e:
        logger.debug("pipeline_sessions upsert failed for %s: %s",
                      session_id, e)


def mark_running_as_interrupted(db_path: str) -> int:
    """On server boot, flip every ``status='running'`` row to
    ``'interrupted'`` — the original asyncio.Task is gone and there's
    no way to resume. Returns the row count that was flipped (handy
    for boot logs).

    Called from ``ui/backend/app/main.py`` startup hook.
    """
    if not db_path:
        return 0
    now = time.time()
    try:
        with _conn(db_path) as c:
            cur = c.execute(
                """
                UPDATE pipeline_sessions
                   SET status = 'interrupted',
                       updated_at = ?,
                       completed_at = COALESCE(completed_at, ?)
                 WHERE status IN ('running', 'paused_audit_review')
                """,
                (now, now),
            )
            return cur.rowcount or 0
    except Exception as e:
        logger.warning("mark_running_as_interrupted failed: %s", e)
        return 0


# ─────────── Read path ───────────


def get_session(db_path: str, session_id: str) -> dict[str, Any] | None:
    """Fetch a single persisted session by id."""
    if not db_path or not session_id:
        return None
    try:
        with _conn(db_path) as c:
            row = c.execute(
                "SELECT * FROM pipeline_sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
    except Exception as e:
        logger.debug("get_session failed for %s: %s", session_id, e)
        return None
    if not row:
        return None
    return _row_to_dict(row)


def list_sessions(
    db_path: str,
    *,
    project_id: str = "",
    chapter_id: str = "",
    status_filter: str | list[str] = "",
    limit: int = 50,
) -> list[dict[str, Any]]:
    """List session rows, newest first. Optional filters:

      * ``project_id`` — only sessions for this project
      * ``chapter_id`` — only sessions for this chapter
      * ``status_filter`` — single status or list of statuses
    """
    if not db_path:
        return []
    where: list[str] = []
    params: list[Any] = []
    if project_id:
        where.append("project_id = ?")
        params.append(project_id)
    if chapter_id:
        where.append("chapter_id = ?")
        params.append(chapter_id)
    if status_filter:
        statuses = (
            [status_filter] if isinstance(status_filter, str) else list(status_filter)
        )
        statuses = [s for s in statuses if s]
        if statuses:
            placeholders = ",".join("?" * len(statuses))
            where.append(f"status IN ({placeholders})")
            params.extend(statuses)
    sql = "SELECT * FROM pipeline_sessions"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY created_at DESC LIMIT ?"
    params.append(int(limit))
    try:
        with _conn(db_path) as c:
            rows = c.execute(sql, params).fetchall()
    except Exception as e:
        logger.debug("list_sessions failed: %s", e)
        return []
    return [_row_to_dict(r) for r in rows]


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    """Convert a row to the public dict shape. JSON columns are decoded
    back to Python objects (best-effort: bad JSON returns empty)."""
    out: dict[str, Any] = dict(row)
    for k in ("request_json", "events_json", "result_json"):
        public_key = k.replace("_json", "")
        raw = out.pop(k, "")
        if raw:
            try:
                out[public_key] = json.loads(raw)
            except Exception:
                out[public_key] = None
        else:
            out[public_key] = None
    # Normalize booleans
    out["waiting_confirm"] = bool(out.get("waiting_confirm"))
    out["waiting_manual"] = bool(out.get("waiting_manual"))
    return out
