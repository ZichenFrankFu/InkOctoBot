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

    # ── Phase 3: state_change_log (append-only audit) ──
    """
    CREATE TABLE IF NOT EXISTS state_change_log (
        log_id               TEXT PRIMARY KEY,
        project_id           TEXT NOT NULL,
        subject              TEXT NOT NULL,
        subject_type         TEXT NOT NULL DEFAULT 'character',
        predicate            TEXT NOT NULL,
        old_value            TEXT,
        new_value            TEXT,
        change_chapter       INTEGER NOT NULL,
        trigger              TEXT,
        source               TEXT NOT NULL DEFAULT 'llm_auto'
                              CHECK (source IN (
                                  'llm_auto', 'user_manual', 'user_correction'
                              )),
        change_type          TEXT NOT NULL DEFAULT 'state_change'
                              CHECK (change_type IN (
                                  'state_change', 'cancellation', 'correction'
                              )),
        llm_model_used       TEXT,
        validation_warning   TEXT,
        parent_log_id        TEXT,
        recorded_at          TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_state_log_project_chapter "
    "ON state_change_log(project_id, change_chapter)",
    "CREATE INDEX IF NOT EXISTS idx_state_log_subject_predicate "
    "ON state_change_log(subject, predicate, change_chapter)",

    # ── Phase 3: entity_review_queue (suspected-duplicate review) ──
    """
    CREATE TABLE IF NOT EXISTS entity_review_queue (
        queue_id                    TEXT PRIMARY KEY,
        project_id                  TEXT NOT NULL,
        chapter_id                  TEXT NOT NULL,
        candidate_name              TEXT NOT NULL,
        candidate_type              TEXT NOT NULL,
        candidate_aliases_json      TEXT NOT NULL DEFAULT '[]',
        similar_existing_entity_id  TEXT,
        similarity_score            REAL,
        llm_extraction_context      TEXT,
        status                      TEXT NOT NULL DEFAULT 'pending'
                                     CHECK (status IN (
                                         'pending', 'merged',
                                         'created_separately', 'discarded'
                                     )),
        user_decision_at            TIMESTAMP,
        created_at                  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_entity_review_status "
    "ON entity_review_queue(project_id, status)",
    "CREATE INDEX IF NOT EXISTS idx_entity_review_chapter "
    "ON entity_review_queue(chapter_id)",

    # ── Phase 4: chapter_snapshots (version history) ──
    """
    CREATE TABLE IF NOT EXISTS chapter_snapshots (
        snapshot_id                       TEXT PRIMARY KEY,
        project_id                        TEXT NOT NULL,
        chapter_id                        TEXT NOT NULL,
        chapter_num                       INTEGER NOT NULL,
        committed_at                      TIMESTAMP NOT NULL
                                            DEFAULT CURRENT_TIMESTAMP,
        chapter_outline_snapshot          TEXT,
        on_stage_entities_snapshot        TEXT,
        special_requirements_snapshot     TEXT,
        project_config_hash               TEXT,
        characters_config_hash            TEXT,
        worldbook_hash                    TEXT,
        full_prompt_snapshot              TEXT,
        diagnostics_snapshot              TEXT,
        generation_model_used             TEXT,
        embedding_model_used              TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_chapter_snapshots_chapter "
    "ON chapter_snapshots(chapter_id, committed_at)",
    "CREATE INDEX IF NOT EXISTS idx_chapter_snapshots_project "
    "ON chapter_snapshots(project_id, chapter_num)",

    # ── Phase 4: chapter_config_snapshots (hash-deduped store) ──
    """
    CREATE TABLE IF NOT EXISTS chapter_config_snapshots (
        config_hash          TEXT PRIMARY KEY,
        config_type          TEXT NOT NULL,
        full_content         TEXT NOT NULL,
        first_referenced_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,

    # ── v3.1 确认 gate (spec Post-Commit·机制3 + LLM交互·机制2):
    #    when post_commit_require_confirmation is on, the merged
    #    state extraction lands here as a proposal; the user applies
    #    or discards it before anything touches the canonical tables. ──
    """
    CREATE TABLE IF NOT EXISTS pending_state_extractions (
        proposal_id   TEXT PRIMARY KEY,
        project_id    TEXT NOT NULL,
        chapter_id    TEXT NOT NULL,
        chapter_num   INTEGER NOT NULL,
        parsed_json   TEXT NOT NULL,
        llm_model     TEXT NOT NULL DEFAULT '',
        status        TEXT NOT NULL DEFAULT 'pending'
            CHECK (status IN ('pending','applied','discarded')),
        created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        resolved_at   TIMESTAMP
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_pending_extractions "
    "ON pending_state_extractions(project_id, status)",
)


# Columns added to existing tables to support Phase 3 (storyland_entities
# attribution + mention tracking). Idempotent ALTER block.
_STORYLAND_ENTITIES_PC3_COLUMNS = [
    ("mention_count",   "INTEGER NOT NULL DEFAULT 0"),
    ("auto_created",    "INTEGER NOT NULL DEFAULT 0"),
]


def _ensure_storyland_entities_pc3_columns(conn: sqlite3.Connection) -> None:
    cur = conn.cursor()
    try:
        existing = {row[1] for row in cur.execute(
            "PRAGMA table_info(storyland_entities)",
        )}
    except sqlite3.OperationalError:
        return
    for col, ddl in _STORYLAND_ENTITIES_PC3_COLUMNS:
        if col not in existing:
            cur.execute(f"ALTER TABLE storyland_entities ADD COLUMN {col} {ddl}")


def ensure_post_commit_tables(conn: sqlite3.Connection) -> None:
    """Create all Phase 1-3 post-commit tables + indexes; add columns
    to legacy tables that need attribution / metadata.

    Idempotent. Called from ``ensure_creation_tables`` so every project
    DB picks up the schema on next backend startup.
    """
    cur = conn.cursor()
    for ddl in _DDL:
        cur.execute(ddl)
    conn.commit()
    _ensure_storyland_entities_pc3_columns(conn)
    conn.commit()
