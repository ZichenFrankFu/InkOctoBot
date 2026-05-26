"""
truth_schema.py — DDL for the Truth File system (7 canonical truth files).

Tables:
  - truth_current_state    : SPO triples with chapter validity windows
  - character_ledger       : particle ledger (resource/item accounting)
  - pending_hooks          : foreshadow state machine
  - hook_events            : audit log of every hook transition
  - subplot_threads        : parallel narrative threads
  - emotion_arcs           : per-character emotion trajectory entries
  - character_relations    : pairwise relationships (A->B view)
  - truth_apply_log        : idempotency + audit log for apply_deltas

chapter_summaries is re-used from creation_schema.py (no new DDL).
"""
from __future__ import annotations

import sqlite3


TRUTH_DDL = [
    # ── 1. truth_current_state ─────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS truth_current_state (
        triple_id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL,
        subject TEXT NOT NULL,
        predicate TEXT NOT NULL,
        object TEXT NOT NULL,
        valid_from_chapter INTEGER NOT NULL,
        valid_to_chapter INTEGER,
        superseded_by TEXT,
        confidence REAL DEFAULT 1.0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (project_id) REFERENCES projects(project_id) ON DELETE CASCADE
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_state_subject ON truth_current_state(project_id, subject);",
    "CREATE INDEX IF NOT EXISTS idx_state_predicate ON truth_current_state(project_id, predicate);",
    "CREATE INDEX IF NOT EXISTS idx_state_window ON truth_current_state(project_id, valid_from_chapter, valid_to_chapter);",

    # ── 2. character_ledger ────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS character_ledger (
        ledger_id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL,
        character_name TEXT NOT NULL,
        chapter_num INTEGER NOT NULL,
        category TEXT NOT NULL,
        key TEXT NOT NULL,
        operation TEXT NOT NULL CHECK(operation IN ('add','subtract','set')),
        delta INTEGER NOT NULL,
        new_value INTEGER,
        reason TEXT,
        in_text_evidence TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (project_id) REFERENCES projects(project_id) ON DELETE CASCADE
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_ledger_lookup ON character_ledger(project_id, character_name, key, chapter_num);",
    "CREATE INDEX IF NOT EXISTS idx_ledger_chapter ON character_ledger(project_id, chapter_num);",

    # ── 3. pending_hooks ───────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS pending_hooks (
        hook_id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL,
        description TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'open'
            CHECK(status IN ('open','progressing','pressured','near_payoff','resolved','abandoned')),
        importance TEXT NOT NULL DEFAULT 'B' CHECK(importance IN ('A','B','C')),
        origin_chapter INTEGER NOT NULL,
        expected_payoff_chapter INTEGER,
        last_mention_chapter INTEGER,
        last_advance_chapter INTEGER,
        pressure_threshold INTEGER NOT NULL DEFAULT 5,
        tags_json TEXT NOT NULL DEFAULT '[]',
        -- A1 InkOS spoiler-filter (v2.2): when 1, this hook leaks future
        -- info and should only be visible to the omniscient narrator
        -- bundle, not to character-POV bundles. revealed_to_chars_json
        -- is a JSON array of character names who DO know it (in-fiction).
        is_spoiler INTEGER NOT NULL DEFAULT 0,
        revealed_to_chars_json TEXT NOT NULL DEFAULT '[]',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (project_id) REFERENCES projects(project_id) ON DELETE CASCADE
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_hooks_proj_status ON pending_hooks(project_id, status);",
    "CREATE INDEX IF NOT EXISTS idx_hooks_advance ON pending_hooks(project_id, last_advance_chapter);",

    """
    CREATE TABLE IF NOT EXISTS hook_events (
        event_id TEXT PRIMARY KEY,
        hook_id TEXT NOT NULL,
        project_id TEXT NOT NULL,
        chapter_num INTEGER NOT NULL,
        action TEXT NOT NULL CHECK(action IN ('new','mention','progress','resolve','abandon')),
        before_status TEXT,
        after_status TEXT,
        evidence TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (hook_id) REFERENCES pending_hooks(hook_id) ON DELETE CASCADE
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_hook_events_hook ON hook_events(hook_id, chapter_num);",

    # ── 4. chapter_summaries — re-used from creation_schema (no DDL) ──

    # ── 5. subplot_threads ─────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS subplot_threads (
        thread_id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL,
        name TEXT NOT NULL,
        description TEXT,
        status TEXT NOT NULL DEFAULT 'setup'
            CHECK(status IN ('setup','building','climax','resolution','dormant')),
        start_chapter INTEGER NOT NULL,
        last_advanced_chapter INTEGER,
        related_hook_ids_json TEXT NOT NULL DEFAULT '[]',
        related_character_ids_json TEXT NOT NULL DEFAULT '[]',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (project_id) REFERENCES projects(project_id) ON DELETE CASCADE
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_subplots_proj_status ON subplot_threads(project_id, status);",

    # ── 6. emotion_arcs ────────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS emotion_arcs (
        arc_id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL,
        character_name TEXT NOT NULL,
        chapter_num INTEGER NOT NULL,
        from_state TEXT NOT NULL,
        to_state TEXT NOT NULL,
        trigger TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (project_id) REFERENCES projects(project_id) ON DELETE CASCADE
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_emotion_char ON emotion_arcs(project_id, character_name, chapter_num);",

    # ── 7. character_relations ─────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS character_relations (
        rel_id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL,
        character_a TEXT NOT NULL,
        character_b TEXT NOT NULL,
        relation_type TEXT NOT NULL,
        sentiment_score INTEGER NOT NULL CHECK(sentiment_score BETWEEN -100 AND 100),
        trust_level INTEGER NOT NULL CHECK(trust_level BETWEEN 0 AND 100),
        last_interaction_chapter INTEGER,
        note TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (project_id) REFERENCES projects(project_id) ON DELETE CASCADE,
        UNIQUE(project_id, character_a, character_b)
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_relations_proj ON character_relations(project_id, character_a);",

    # ── Apply log: idempotency + audit ─────────────────────────
    """
    CREATE TABLE IF NOT EXISTS truth_apply_log (
        apply_id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL,
        chapter_num INTEGER NOT NULL,
        deltas_hash TEXT NOT NULL,
        applied_counts_json TEXT,
        cross_ref_issues_json TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """,
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_truth_apply_idempotent ON truth_apply_log(project_id, deltas_hash);",
    "CREATE INDEX IF NOT EXISTS idx_truth_apply_chapter ON truth_apply_log(project_id, chapter_num);",
]


def ensure_truth_tables(conn: sqlite3.Connection) -> None:
    """Create all Truth File tables. Idempotent.

    Also upgrades v1-style pending_hooks (no is_spoiler / revealed_to_chars_json
    columns) to v2.2 via ALTER. Safe to call on fresh + upgraded DBs.
    """
    cur = conn.cursor()
    cur.execute("PRAGMA foreign_keys = ON;")
    for ddl in TRUTH_DDL:
        cur.execute(ddl)

    # v1 → v2.2 ALTER: add spoiler-filter columns if missing.
    try:
        cols = {row[1] for row in cur.execute("PRAGMA table_info(pending_hooks)")}
        if "is_spoiler" not in cols:
            cur.execute(
                "ALTER TABLE pending_hooks ADD COLUMN "
                "is_spoiler INTEGER NOT NULL DEFAULT 0"
            )
        if "revealed_to_chars_json" not in cols:
            cur.execute(
                "ALTER TABLE pending_hooks ADD COLUMN "
                "revealed_to_chars_json TEXT NOT NULL DEFAULT '[]'"
            )
    except sqlite3.OperationalError:
        # Table missing → CREATE above already handles it; ignore.
        pass

    conn.commit()
