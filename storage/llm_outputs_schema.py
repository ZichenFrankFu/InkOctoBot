"""Unified audit table for every LLM call across the codebase.

Every ``LLMCallSite.invoke()`` writes one row here regardless of:
- auto vs manual mode (``source``)
- which call site produced it (``call_site_id``)
- which target table the parsed output landed in (``parsed_target_table`` /
  ``parsed_target_row_id``)

The existing per-call-site review tables (``chapter_summaries`` /
``state_change_log`` / ``platform_profiles`` / etc.) keep their CRUD;
this layer is a uniform audit + manual-mode anchor.
"""
from __future__ import annotations

import sqlite3


_DDL: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS llm_outputs (
        output_id              TEXT PRIMARY KEY,
        call_site_id           TEXT NOT NULL,
        project_id             TEXT,
        chapter_id             TEXT,
        prompt_hash            TEXT NOT NULL,
        prompt_full            TEXT NOT NULL,
        system_prompt          TEXT,
        response_raw           TEXT NOT NULL,
        parsed_target_table    TEXT,
        parsed_target_row_id   TEXT,
        source                 TEXT NOT NULL DEFAULT 'auto'
                                CHECK (source IN ('auto', 'manual')),
        provider               TEXT,
        model                  TEXT,
        token_count_prompt     INTEGER,
        token_count_response   INTEGER,
        cost_usd               REAL,
        duration_ms            REAL,
        created_at             TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        user_acknowledged      INTEGER NOT NULL DEFAULT 0,
        user_corrected         INTEGER NOT NULL DEFAULT 0
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_llm_outputs_site "
    "ON llm_outputs(call_site_id, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_llm_outputs_project "
    "ON llm_outputs(project_id, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_llm_outputs_chapter "
    "ON llm_outputs(chapter_id)",
    "CREATE INDEX IF NOT EXISTS idx_llm_outputs_target "
    "ON llm_outputs(parsed_target_table, parsed_target_row_id)",
    "CREATE INDEX IF NOT EXISTS idx_llm_outputs_unacked "
    "ON llm_outputs(user_acknowledged, created_at)",
)


def ensure_llm_outputs_table(conn: sqlite3.Connection) -> None:
    """Idempotent table + indexes. Hook into ensure_creation_tables."""
    cur = conn.cursor()
    for ddl in _DDL:
        cur.execute(ddl)
    conn.commit()
