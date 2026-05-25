"""
creation_schema.py — DDL for creation workflow + memory system tables.

Covers: projects, chapters, versions, memory layers 2/4,
information events (knowledge isolation), user style preferences.
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
]


def ensure_creation_tables(conn: sqlite3.Connection) -> None:
    """Create all creation + memory tables, plus the Truth File system tables."""
    cur = conn.cursor()
    cur.execute("PRAGMA foreign_keys = ON;")
    for ddl in CREATION_DDL:
        cur.execute(ddl)
    conn.commit()

    from database.truth_schema import ensure_truth_tables
    ensure_truth_tables(conn)
