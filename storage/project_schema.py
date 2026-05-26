"""
project_schema.py — DDL for project workflow + memory + canonical entities.

v2 (see docs/SCHEMA_REDESIGN.md): in addition to the legacy memory and
creation tables, all per-project entities that used to live as JSON
files under ``data/`` are now first-class tables here:
  - characters, worldbook_entries, project_memories
  - storyline_nodes, storyline_edges
  - writing_knowledge, chat_messages
  - project_blobs (single-row KV per project: editor doc, calibration, ...)
"""
from __future__ import annotations

import sqlite3

CREATION_DDL = [
    # ── Projects ──
    """
    CREATE TABLE IF NOT EXISTS projects (
        project_id TEXT PRIMARY KEY,
        title TEXT NOT NULL DEFAULT '',
        genre TEXT NOT NULL DEFAULT '',
        logline TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'planning'
            CHECK (status IN ('planning','writing','paused','completed')),
        current_chapter INTEGER NOT NULL DEFAULT 0,
        current_volume INTEGER NOT NULL DEFAULT 1,
        world_book_path TEXT NOT NULL DEFAULT '',
        style_profile_json TEXT NOT NULL DEFAULT '{}',
        model_preset TEXT NOT NULL DEFAULT 'balanced',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """,

    # ── Chapters ──
    # v2: added synopsis/time_label/location/characters_json/pov_character
    # /extra_json so the editor doc JSON file becomes obsolete. The added
    # columns are appended via ALTER below for backward compat with v1 DBs.
    """
    CREATE TABLE IF NOT EXISTS chapters (
        chapter_id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL,
        volume INTEGER NOT NULL DEFAULT 1,
        chapter_num INTEGER NOT NULL,
        title TEXT NOT NULL DEFAULT '',
        outline TEXT NOT NULL DEFAULT '',
        final_text TEXT NOT NULL DEFAULT '',
        word_count INTEGER NOT NULL DEFAULT 0,
        status TEXT NOT NULL DEFAULT 'draft'
            CHECK (status IN ('draft','scene_planned','acted','edited','evaluated','finalized')),
        scene_plan_json TEXT NOT NULL DEFAULT '[]',
        performance_log TEXT NOT NULL DEFAULT '',
        evaluation_json TEXT NOT NULL DEFAULT '{}',
        synopsis TEXT NOT NULL DEFAULT '',
        time_label TEXT NOT NULL DEFAULT '',
        location TEXT NOT NULL DEFAULT '',
        characters_json TEXT NOT NULL DEFAULT '[]',
        pov_character TEXT NOT NULL DEFAULT '',
        extra_json TEXT NOT NULL DEFAULT '{}',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (project_id) REFERENCES projects(project_id) ON DELETE CASCADE,
        UNIQUE(project_id, chapter_num)
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_chapters_project ON chapters(project_id, chapter_num);",

    # ── Text versions (version chain) ──
    """
    CREATE TABLE IF NOT EXISTS text_versions (
        version_id TEXT PRIMARY KEY,
        chapter_id TEXT NOT NULL,
        version_num INTEGER NOT NULL,
        source TEXT NOT NULL DEFAULT 'ai'
            CHECK (source IN ('ai','user_edit','rewrite')),
        content TEXT NOT NULL DEFAULT '',
        diff_json TEXT NOT NULL DEFAULT '{}',
        model_used TEXT NOT NULL DEFAULT '',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (chapter_id) REFERENCES chapters(chapter_id) ON DELETE CASCADE
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_versions_chapter ON text_versions(chapter_id, version_num);",

    # ── Layer 2: Chapter Buffer (summaries) ──
    """
    CREATE TABLE IF NOT EXISTS chapter_summaries (
        summary_id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL,
        chapter_num INTEGER NOT NULL,
        summary_text TEXT NOT NULL DEFAULT '',
        key_events_json TEXT NOT NULL DEFAULT '[]',
        character_states_json TEXT NOT NULL DEFAULT '{}',
        is_active INTEGER NOT NULL DEFAULT 1,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (project_id) REFERENCES projects(project_id) ON DELETE CASCADE,
        UNIQUE(project_id, chapter_num)
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_summaries_project ON chapter_summaries(project_id, is_active);",

    # ── Layer 4: Episodic Timeline ──
    """
    CREATE TABLE IF NOT EXISTS episodic_events (
        event_id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL,
        chapter_num INTEGER NOT NULL,
        scene_index INTEGER NOT NULL DEFAULT 0,
        event_type TEXT NOT NULL DEFAULT 'plot'
            CHECK (event_type IN ('plot','character_change','revelation',
                                  'foreshadowing','foreshadowing_payoff',
                                  'relationship_change','world_change')),
        description TEXT NOT NULL DEFAULT '',
        characters_json TEXT NOT NULL DEFAULT '[]',
        causality_json TEXT NOT NULL DEFAULT '{}',
        foreshadow_status TEXT DEFAULT NULL
            CHECK (foreshadow_status IN (NULL,'planted','partially_resolved','resolved','abandoned')),
        foreshadow_target_chapter INTEGER DEFAULT NULL,
        importance INTEGER NOT NULL DEFAULT 3
            CHECK (importance BETWEEN 1 AND 5),
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (project_id) REFERENCES projects(project_id) ON DELETE CASCADE
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_episodic_project ON episodic_events(project_id, chapter_num);",
    "CREATE INDEX IF NOT EXISTS idx_episodic_foreshadow ON episodic_events(project_id, foreshadow_status);",
    "CREATE INDEX IF NOT EXISTS idx_episodic_type ON episodic_events(project_id, event_type);",

    # ── Knowledge isolation: information events ──
    """
    CREATE TABLE IF NOT EXISTS information_events (
        info_id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL,
        character_name TEXT NOT NULL,
        fact_key TEXT NOT NULL,
        knowledge_state TEXT NOT NULL DEFAULT 'unknown'
            CHECK (knowledge_state IN ('known_true','known_false','unknown')),
        believed_value TEXT NOT NULL DEFAULT '',
        true_value TEXT NOT NULL DEFAULT '',
        source_chapter INTEGER NOT NULL DEFAULT 0,
        source_description TEXT NOT NULL DEFAULT '',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (project_id) REFERENCES projects(project_id) ON DELETE CASCADE
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_info_char ON information_events(project_id, character_name);",
    "CREATE INDEX IF NOT EXISTS idx_info_fact ON information_events(project_id, fact_key);",

    # ── Permanent facts (from Layer 2 compression) ──
    """
    CREATE TABLE IF NOT EXISTS permanent_facts (
        fact_id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL,
        fact_type TEXT NOT NULL DEFAULT 'permanent'
            CHECK (fact_type IN ('permanent','active_foreshadowing','character_state_change')),
        content TEXT NOT NULL DEFAULT '',
        source_chapter INTEGER NOT NULL DEFAULT 0,
        characters_json TEXT NOT NULL DEFAULT '[]',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (project_id) REFERENCES projects(project_id) ON DELETE CASCADE
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_perm_project ON permanent_facts(project_id, fact_type);",

    # ── User style preferences (from EditAnalyzer) ──
    """
    CREATE TABLE IF NOT EXISTS user_style_preferences (
        pref_id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL,
        preference_type TEXT NOT NULL DEFAULT 'style'
            CHECK (preference_type IN ('style','content','pacing')),
        description TEXT NOT NULL DEFAULT '',
        confidence REAL NOT NULL DEFAULT 0.0,
        observation_count INTEGER NOT NULL DEFAULT 0,
        examples_json TEXT NOT NULL DEFAULT '[]',
        is_confirmed INTEGER NOT NULL DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (project_id) REFERENCES projects(project_id) ON DELETE CASCADE
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_prefs_project ON user_style_preferences(project_id, preference_type);",

    # ── Constraint rules (persistent) ──
    """
    CREATE TABLE IF NOT EXISTS constraint_rules (
        rule_id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL,
        rule_type TEXT NOT NULL DEFAULT 'world_rule'
            CHECK (rule_type IN ('world_rule','knowledge_isolation',
                                 'plot_constraint','style_constraint')),
        priority INTEGER NOT NULL DEFAULT 3
            CHECK (priority BETWEEN 1 AND 5),
        description TEXT NOT NULL DEFAULT '',
        positive_restatement TEXT NOT NULL DEFAULT '',
        good_example TEXT NOT NULL DEFAULT '',
        bad_example TEXT NOT NULL DEFAULT '',
        is_active INTEGER NOT NULL DEFAULT 1,
        source TEXT NOT NULL DEFAULT 'user',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (project_id) REFERENCES projects(project_id) ON DELETE CASCADE
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_constraints_project ON constraint_rules(project_id, rule_type, is_active);",

    # ══════════════════════════════════════════════════════════════════
    # v2 canonical entity tables (replace data/{characters,worldbook,...} JSON)
    # See docs/SCHEMA_REDESIGN.md for rationale.
    # ══════════════════════════════════════════════════════════════════

    # ── Characters (replaces data/characters/*.json) ──
    """
    CREATE TABLE IF NOT EXISTS characters (
        character_id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL,
        name TEXT NOT NULL,
        role TEXT NOT NULL DEFAULT '',
        description TEXT NOT NULL DEFAULT '',
        personality TEXT NOT NULL DEFAULT '',
        background TEXT NOT NULL DEFAULT '',
        appearance TEXT NOT NULL DEFAULT '',
        speech_style TEXT NOT NULL DEFAULT '',
        tags_json TEXT NOT NULL DEFAULT '[]',
        relationships_json TEXT NOT NULL DEFAULT '[]',
        layer_a_json TEXT NOT NULL DEFAULT '{}',
        layer_b_json TEXT NOT NULL DEFAULT '{}',
        extra_json TEXT NOT NULL DEFAULT '{}',
        sort_order INTEGER NOT NULL DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (project_id) REFERENCES projects(project_id) ON DELETE CASCADE,
        UNIQUE(project_id, name)
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_characters_project ON characters(project_id, sort_order);",

    # ── Worldbook entries (replaces data/worldbook/*.json) ──
    """
    CREATE TABLE IF NOT EXISTS worldbook_entries (
        entry_id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL,
        title TEXT NOT NULL,
        category TEXT NOT NULL DEFAULT 'misc',
        content TEXT NOT NULL DEFAULT '',
        tags_json TEXT NOT NULL DEFAULT '[]',
        sort_order INTEGER NOT NULL DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (project_id) REFERENCES projects(project_id) ON DELETE CASCADE
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_worldbook_project ON worldbook_entries(project_id, category);",

    # ── Project memories (replaces data/project_memory/<pid>.json) ──
    """
    CREATE TABLE IF NOT EXISTS project_memories (
        memory_id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL,
        category TEXT NOT NULL DEFAULT 'note',
        content TEXT NOT NULL DEFAULT '',
        source TEXT NOT NULL DEFAULT 'user',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (project_id) REFERENCES projects(project_id) ON DELETE CASCADE
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_project_memories_project ON project_memories(project_id, category);",

    # ── Storyline nodes + edges (replaces data/storylines/<pid>.json) ──
    """
    CREATE TABLE IF NOT EXISTS storyline_nodes (
        node_id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL,
        title TEXT NOT NULL DEFAULT '',
        chapter_num INTEGER,
        summary TEXT NOT NULL DEFAULT '',
        time_label TEXT NOT NULL DEFAULT '',
        location TEXT NOT NULL DEFAULT '',
        pos_x REAL NOT NULL DEFAULT 0,
        pos_y REAL NOT NULL DEFAULT 0,
        extra_json TEXT NOT NULL DEFAULT '{}',
        FOREIGN KEY (project_id) REFERENCES projects(project_id) ON DELETE CASCADE
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_storyline_nodes_project ON storyline_nodes(project_id);",
    """
    CREATE TABLE IF NOT EXISTS storyline_edges (
        edge_id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL,
        from_node_id TEXT NOT NULL,
        to_node_id TEXT NOT NULL,
        label TEXT NOT NULL DEFAULT '',
        FOREIGN KEY (project_id) REFERENCES projects(project_id) ON DELETE CASCADE,
        FOREIGN KEY (from_node_id) REFERENCES storyline_nodes(node_id) ON DELETE CASCADE,
        FOREIGN KEY (to_node_id) REFERENCES storyline_nodes(node_id) ON DELETE CASCADE
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_storyline_edges_project ON storyline_edges(project_id, from_node_id);",

    # ── Writing knowledge (replaces data/writing_knowledge/*.json) ──
    # Cross-project: no project_id (reusable craft notes).
    """
    CREATE TABLE IF NOT EXISTS writing_knowledge (
        knowledge_id TEXT PRIMARY KEY,
        domain TEXT NOT NULL DEFAULT 'general',
        title TEXT NOT NULL,
        content TEXT NOT NULL DEFAULT '',
        tags_json TEXT NOT NULL DEFAULT '[]',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_writing_knowledge_domain ON writing_knowledge(domain);",

    # ── Chat messages (replaces data/chat_history/*.json) ──
    """
    CREATE TABLE IF NOT EXISTS chat_messages (
        message_id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL,
        scope TEXT NOT NULL DEFAULT 'pipeline',
        role TEXT NOT NULL CHECK(role IN ('user','assistant','system','tool')),
        content TEXT NOT NULL DEFAULT '',
        meta_json TEXT NOT NULL DEFAULT '{}',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (project_id) REFERENCES projects(project_id) ON DELETE CASCADE
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_chat_project_scope ON chat_messages(project_id, scope, created_at);",

    # ── Project blobs — single-row-per-project KV
    # (editor doc, calibration, reference_injection, knowledge_injection,
    #  foreshadowing_legacy, ...) ──
    """
    CREATE TABLE IF NOT EXISTS project_blobs (
        blob_id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL,
        scope TEXT NOT NULL,
        data_json TEXT NOT NULL DEFAULT '{}',
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (project_id) REFERENCES projects(project_id) ON DELETE CASCADE,
        UNIQUE(project_id, scope)
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_project_blobs_lookup ON project_blobs(project_id, scope);",
]


# ── ALTER TABLE upgrade map for v1 -> v2 ───────────────────────────
# (column_name, DDL fragment). Applied via ``_ensure_chapter_v2_columns``
# below. Idempotent — checks PRAGMA table_info first.
_CHAPTERS_V2_COLUMNS = [
    ("synopsis",        "TEXT NOT NULL DEFAULT ''"),
    ("time_label",      "TEXT NOT NULL DEFAULT ''"),
    ("location",        "TEXT NOT NULL DEFAULT ''"),
    ("characters_json", "TEXT NOT NULL DEFAULT '[]'"),
    ("pov_character",   "TEXT NOT NULL DEFAULT ''"),
    ("extra_json",      "TEXT NOT NULL DEFAULT '{}'"),
]


def _ensure_chapter_v2_columns(conn: sqlite3.Connection) -> None:
    """Add v2 columns to an existing v1 chapters table.

    SQLite refuses to add columns with non-constant defaults, so each
    added column has a constant DEFAULT and we never see a NOT NULL
    violation on existing rows.
    """
    cur = conn.cursor()
    existing = {row[1] for row in cur.execute("PRAGMA table_info(chapters)")}
    for col, ddl in _CHAPTERS_V2_COLUMNS:
        if col not in existing:
            cur.execute(f"ALTER TABLE chapters ADD COLUMN {col} {ddl}")


def ensure_creation_tables(conn: sqlite3.Connection) -> None:
    """Create all project tables (v2 schema), plus Truth File tables.

    Idempotent: safe to call on a fresh DB or one that already has the
    v1 schema. Adds v2-only columns / tables in-place.
    """
    cur = conn.cursor()
    cur.execute("PRAGMA foreign_keys = ON;")
    for ddl in CREATION_DDL:
        cur.execute(ddl)
    _ensure_chapter_v2_columns(conn)
    conn.commit()

    from storage.truth_schema import ensure_truth_tables
    ensure_truth_tables(conn)
