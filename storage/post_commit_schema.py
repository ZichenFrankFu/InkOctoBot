"""Post-commit pipeline schema — commit_tasks + user_notifications.

Both tables live alongside the rest of the project DB (the same SQLite
file used for ``projects``, ``chapters``, etc.). They're queried from
the same connection the chapter-commit endpoint already opens, so
keeping them co-resident avoids cross-DB joins down the line.

``ensure_post_commit_tables(conn)`` is idempotent — safe to call on a
fresh DB or one that's already been initialized. Hook it into
``ensure_creation_tables`` so every existing project picks it up on
the next backend boot.
"""
from __future__ import annotations

import sqlite3

# Each DDL is wrapped in IF NOT EXISTS so the call is replayable.
_DDL: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS commit_tasks (
        task_id              TEXT PRIMARY KEY,
        project_id           TEXT NOT NULL,
        chapter_id           TEXT NOT NULL,
        task_type            TEXT NOT NULL,
        state                TEXT NOT NULL DEFAULT 'pending'
                              CHECK (state IN (
                                  'pending', 'running', 'completed',
                                  'failed_will_retry',
                                  'failed_needs_manual',
                                  'skipped', 'cancelled'
                              )),
        retry_count          INTEGER NOT NULL DEFAULT 0,
        last_error_message   TEXT,
        last_error_stack     TEXT,
        started_at           TIMESTAMP,
        last_attempt_at      TIMESTAMP,
        completed_at         TIMESTAMP,
        output_summary       TEXT,
        user_notified        INTEGER NOT NULL DEFAULT 0,
        user_acknowledged    INTEGER NOT NULL DEFAULT 0,
        manual_action_url    TEXT,
        created_at           TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_commit_tasks_chapter "
    "ON commit_tasks(chapter_id)",
    "CREATE INDEX IF NOT EXISTS idx_commit_tasks_project_state "
    "ON commit_tasks(project_id, state)",
    """
    CREATE TABLE IF NOT EXISTS user_notifications (
        notification_id      TEXT PRIMARY KEY,
        project_id           TEXT NOT NULL,
        chapter_id           TEXT,
        notification_type    TEXT NOT NULL,
        severity             TEXT NOT NULL
                              CHECK (severity IN ('high', 'medium', 'low')),
        title                TEXT NOT NULL,
        description          TEXT,
        action_label         TEXT,
        action_url           TEXT,
        related_task_id      TEXT,
        related_entity_id    TEXT,
        created_at           TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        read_at              TIMESTAMP,
        acknowledged_at      TIMESTAMP,
        auto_dismiss_at      TIMESTAMP
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_notif_unread "
    "ON user_notifications(project_id, read_at)",
    "CREATE INDEX IF NOT EXISTS idx_notif_severity "
    "ON user_notifications(project_id, severity, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_notif_chapter "
    "ON user_notifications(chapter_id)",
)


def ensure_post_commit_tables(conn: sqlite3.Connection) -> None:
    """Create ``commit_tasks`` + ``user_notifications`` tables + indexes.

    Idempotent. Called from ``ensure_creation_tables`` so every project
    DB picks up the schema on next backend startup.
    """
    cur = conn.cursor()
    for ddl in _DDL:
        cur.execute(ddl)
    conn.commit()
