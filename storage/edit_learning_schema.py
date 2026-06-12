"""DDL for the edit-learning + domain-knowledge tables.

Part A: ``edit_observations`` captures per-commit user-edit deltas
(AI baseline + edited text + special_requirement + cheap diff stats).
Once N unconsumed rows accumulate, a batch-extraction pass distils
them into ``user_style_preferences`` AND probes for domain knowledge
gaps that the user should be offered to fill.

Part B: ``domain_suggestions`` is the queue for those domain gaps —
gate 1 (propose / approve / reject), compile step, gate 2 (review /
accept / discard). Only ``accepted`` rows ever materialize as
``agents/knowledge_skills/<slug>/`` artefacts on disk.

Both tables are idempotent (``IF NOT EXISTS``) and FK to projects
with ``ON DELETE CASCADE`` so dropping a project cleans up.
"""
from __future__ import annotations

import sqlite3


_DDL: list[str] = [
    # ── Part A: per-commit edit observations ──
    """
    CREATE TABLE IF NOT EXISTS edit_observations (
        obs_id              TEXT PRIMARY KEY,
        project_id          TEXT NOT NULL,
        chapter_id          TEXT NOT NULL,
        chapter_num         INTEGER NOT NULL DEFAULT 0,
        original_text       TEXT NOT NULL DEFAULT '',
        edited_text         TEXT NOT NULL DEFAULT '',
        special_requirement TEXT NOT NULL DEFAULT '',
        diff_stats_json     TEXT NOT NULL DEFAULT '{}',
        consumed            INTEGER NOT NULL DEFAULT 0,
        created_at          REAL NOT NULL,
        FOREIGN KEY (project_id) REFERENCES projects(project_id) ON DELETE CASCADE
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_edit_obs_project "
    "ON edit_observations(project_id, consumed, created_at);",

    # ── Part B: domain knowledge suggestion queue ──
    """
    CREATE TABLE IF NOT EXISTS domain_suggestions (
        suggestion_id    TEXT PRIMARY KEY,
        project_id       TEXT NOT NULL,
        domain           TEXT NOT NULL,
        sub_domain       TEXT NOT NULL DEFAULT '',
        rationale        TEXT NOT NULL DEFAULT '',
        status           TEXT NOT NULL DEFAULT 'proposed'
            CHECK (status IN (
                'proposed', 'rejected', 'compiling',
                'needs_review', 'accepted', 'failed'
            )),
        compiled_content TEXT NOT NULL DEFAULT '',
        skill_name       TEXT NOT NULL DEFAULT '',
        source_mode      TEXT NOT NULL DEFAULT '',
        created_at       REAL NOT NULL,
        updated_at       REAL NOT NULL,
        FOREIGN KEY (project_id) REFERENCES projects(project_id) ON DELETE CASCADE
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_domain_sug_project "
    "ON domain_suggestions(project_id, status);",
    "CREATE INDEX IF NOT EXISTS idx_domain_sug_domain "
    "ON domain_suggestions(project_id, domain);",
]


def ensure_edit_learning_tables(conn: sqlite3.Connection) -> None:
    """Idempotent DDL hook called from ``ensure_creation_tables``."""
    cur = conn.cursor()
    for ddl in _DDL:
        cur.execute(ddl)
    conn.commit()
