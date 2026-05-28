"""Pipeline session persistence — ``pipeline_sessions`` table.

Stage 5 of the unified-context refactor. Before this stage,
multi-agent sessions lived only in ``_active_sessions`` (in-memory
dict in ``ui/backend/app/routers/generation_api.py``). A server
restart wiped every running session and erased the audit trail of
which chapters were attempted.

This table mirrors the **persistable subset** of the session dict
(everything except ``asyncio.Task`` / ``asyncio.Event`` / the live
``ChapterContext`` object). The pipeline writes here in lockstep
with the in-memory dict; reads still prefer in-memory because
running sessions need the runtime handles.

On boot, ``mark_running_as_interrupted`` flips every ``status='running'``
row to ``'interrupted'`` — the underlying ``asyncio.Task`` is gone so
the session is unrecoverable; the row stays for diagnostics.

Idempotent — safe to call on a fresh DB or one that's already
initialized. Hooked into ``ensure_creation_tables``.
"""
from __future__ import annotations

import sqlite3


_DDL: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS pipeline_sessions (
        session_id        TEXT PRIMARY KEY,
        project_id        TEXT NOT NULL DEFAULT '',
        chapter_id        TEXT NOT NULL DEFAULT '',
        chapter_num       INTEGER NOT NULL DEFAULT 0,
        status            TEXT NOT NULL DEFAULT 'pending'
                           CHECK (status IN (
                               'pending', 'running', 'complete',
                               'error', 'paused_audit_review',
                               'stopped', 'interrupted'
                           )),
        current_step      TEXT NOT NULL DEFAULT '',
        trace_id          TEXT NOT NULL DEFAULT '',
        -- JSON payloads (serialized via json.dumps)
        request_json      TEXT NOT NULL DEFAULT '{}',
        events_json       TEXT NOT NULL DEFAULT '[]',
        result_json       TEXT NOT NULL DEFAULT '',
        text              TEXT NOT NULL DEFAULT '',
        -- UX state
        waiting_confirm   INTEGER NOT NULL DEFAULT 0,
        waiting_manual    INTEGER NOT NULL DEFAULT 0,
        manual_step       TEXT NOT NULL DEFAULT '',
        -- error trail
        error_message     TEXT NOT NULL DEFAULT '',
        -- timestamps (epoch seconds)
        created_at        REAL NOT NULL,
        updated_at        REAL NOT NULL,
        completed_at      REAL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_pipeline_sessions_status "
    "ON pipeline_sessions(status)",
    "CREATE INDEX IF NOT EXISTS idx_pipeline_sessions_project "
    "ON pipeline_sessions(project_id, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_pipeline_sessions_chapter "
    "ON pipeline_sessions(chapter_id, created_at DESC)",
)


def ensure_pipeline_session_table(conn: sqlite3.Connection) -> None:
    """Idempotent — creates ``pipeline_sessions`` + 3 indexes."""
    cur = conn.cursor()
    for stmt in _DDL:
        cur.execute(stmt)
    conn.commit()
