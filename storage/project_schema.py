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
        -- v2.2 InkOS audit gate (B2): Truth Files Phase 2 settlement
        -- result. 'audit_pending'=settlement hasn't run, 'audit_passed'=
        -- no error-severity issues, 'audit_failed'=block finalize until
        -- user reviews + overrides. audit_issues_json holds the concise
        -- CoT-style summary the UI surfaces.
        audit_status TEXT NOT NULL DEFAULT 'audit_pending'
            CHECK (audit_status IN ('audit_pending','audit_passed','audit_failed','audit_overridden')),
        audit_issues_json TEXT NOT NULL DEFAULT '[]',
        special_requirements TEXT NOT NULL DEFAULT '',
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
        is_anchor INTEGER NOT NULL DEFAULT 0,
        title TEXT NOT NULL DEFAULT '',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (project_id) REFERENCES projects(project_id) ON DELETE CASCADE,
        UNIQUE(project_id, chapter_num)
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_summaries_project ON chapter_summaries(project_id, is_active);",
    # idx_summaries_anchor lives in the v3 migration helper — v2 DBs
    # don't have the is_anchor column yet when this DDL list runs.

    # ── Layer 4: Episodic Timeline ──
    # v2: dropped foreshadow_status + foreshadow_target_chapter columns
    # (superseded by Truth Files pending_hooks + hook_events).
    # Also dropped the 'foreshadowing' + 'foreshadowing_payoff' event
    # types from the CHECK constraint — those flows go through
    # HookDelta(action='new'|'resolve'|...) now.
    # v2.1 (MEMORY_VS_TRUTH cleanup): dropped causality_json — was
    # never read, just written as '{}' by every caller.
    """
    CREATE TABLE IF NOT EXISTS episodic_events (
        event_id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL,
        chapter_num INTEGER NOT NULL,
        scene_index INTEGER NOT NULL DEFAULT 0,
        event_type TEXT NOT NULL DEFAULT 'plot'
            CHECK (event_type IN ('plot','character_change','revelation',
                                  'relationship_change','world_change')),
        description TEXT NOT NULL DEFAULT '',
        characters_json TEXT NOT NULL DEFAULT '[]',
        importance INTEGER NOT NULL DEFAULT 3
            CHECK (importance BETWEEN 1 AND 5),
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (project_id) REFERENCES projects(project_id) ON DELETE CASCADE
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_episodic_project ON episodic_events(project_id, chapter_num);",
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

    # ── (removed in v2) permanent_facts table — see
    # docs/SCHEMA_REDESIGN.md. The Truth File
    # ``truth_current_state`` table is canonical for permanent facts.
    # Existing v1 DBs get the table DROPped by
    # ``_drop_v1_redundant_objects`` below.

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
        -- v3.1 自学习 (spec 4.1): 来源章节 + 来源类型
        -- (edit_inference 手动修改推断 / special_requirements 特殊要求)
        source_chapters_json TEXT NOT NULL DEFAULT '[]',
        source_type TEXT NOT NULL DEFAULT 'edit_inference',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (project_id) REFERENCES projects(project_id) ON DELETE CASCADE
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_prefs_project ON user_style_preferences(project_id, preference_type);",

    # ── Operation log (spec EventBus·机制3/机制4: 持久化日志，
    #    可被日志查看器与回溯模式读取) ──
    """
    CREATE TABLE IF NOT EXISTS operation_log (
        log_id TEXT PRIMARY KEY,
        event_type TEXT NOT NULL,
        source TEXT NOT NULL DEFAULT '',
        project_id TEXT NOT NULL DEFAULT '',
        data_json TEXT NOT NULL DEFAULT '{}',
        event_ts REAL NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_oplog_project "
    "ON operation_log(project_id, event_ts);",
    "CREATE INDEX IF NOT EXISTS idx_oplog_type "
    "ON operation_log(event_type, event_ts);",

    # ── Preference learning runs (spec 用户偏好·机制2: 每 N 章批量) ──
    """
    CREATE TABLE IF NOT EXISTS preference_learning_runs (
        run_id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL,
        chapters_covered_json TEXT NOT NULL DEFAULT '[]',
        chapter_count_at_run INTEGER NOT NULL DEFAULT 0,
        prefs_emitted INTEGER NOT NULL DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (project_id) REFERENCES projects(project_id) ON DELETE CASCADE
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_pref_runs_project ON preference_learning_runs(project_id, created_at);",

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
    # v2.1 (MEMORY_VS_TRUTH cleanup): category CHECK constraint enforces
    # the worldbook-vs-characters split — character-shaped categories
    # ('角色','character','主角','配角') are rejected at the SQL layer so
    # the UI is forced to push people-data to the characters table.
    # See docs/MEMORY_VS_TRUTH.md.
    """
    CREATE TABLE IF NOT EXISTS worldbook_entries (
        entry_id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL,
        title TEXT NOT NULL,
        category TEXT NOT NULL DEFAULT '杂项'
            CHECK (category IN (
                -- canonical (Chinese)
                '地点','组织','规则','物品','技术','文化','历史','自然','杂项',
                -- legacy aliases kept readable
                'place','organization','rule','item','tech','culture',
                'history','nature','misc',
                'social_structure','hard_rules','factions','力量体系'
            )),
        content TEXT NOT NULL DEFAULT '',
        tags_json TEXT NOT NULL DEFAULT '[]',
        sort_order INTEGER NOT NULL DEFAULT 0,
        embedding_json TEXT NOT NULL DEFAULT '[]',
        embedding_text_hash TEXT NOT NULL DEFAULT '',
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

    # writing_knowledge table removed in v3.1 — the loader, helpers,
    # router and UI are all gone. _drop_v1_redundant_objects below
    # drops the table on existing DBs so the schema stays clean.

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

    # ── storyland_entities (LOADER_SPEC Loader 10 prerequisite) ────────
    # One registry of all named entities a chapter can put "on stage" —
    # characters, locations, items, organizations, abstract concepts.
    # Auto-synced from characters / worldbook_entries so user-edited
    # tables stay the source of truth; ``manual`` rows are user-created.
    """
    CREATE TABLE IF NOT EXISTS storyland_entities (
        entity_id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL,
        name TEXT NOT NULL,
        entity_type TEXT NOT NULL
            CHECK (entity_type IN
                ('character','location','item','organization','concept')),
        description TEXT NOT NULL DEFAULT '',
        introduced_chapter INTEGER,
        aliases_json TEXT NOT NULL DEFAULT '[]',
        metadata_json TEXT NOT NULL DEFAULT '{}',
        source TEXT NOT NULL DEFAULT 'manual'
            CHECK (source IN ('character','worldbook','manual')),
        source_ref_id TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (project_id) REFERENCES projects(project_id) ON DELETE CASCADE,
        UNIQUE (project_id, name)
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_entities_type ON storyland_entities(project_id, entity_type);",
    "CREATE INDEX IF NOT EXISTS idx_entities_introduced ON storyland_entities(project_id, introduced_chapter);",

    # ── chapter_segments (LOADER_SPEC Loader 9 prerequisite) ───────────
    # Paragraph-level provenance: each segment tracks whether the user
    # wrote it, the AI generated it, or the AI generated it and the user
    # then edited it. Lets the current_chapter_draft loader decide what
    # to preserve in each generation mode.
    """
    CREATE TABLE IF NOT EXISTS chapter_segments (
        segment_id TEXT PRIMARY KEY,
        chapter_id TEXT NOT NULL,
        project_id TEXT NOT NULL,
        sequence_order INTEGER NOT NULL,
        content TEXT NOT NULL,
        source TEXT NOT NULL
            CHECK (source IN ('user_written','ai_generated','ai_user_edited')),
        generation_id TEXT,
        original_ai_content TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        modified_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE (chapter_id, sequence_order),
        FOREIGN KEY (chapter_id) REFERENCES chapters(chapter_id) ON DELETE CASCADE,
        FOREIGN KEY (project_id) REFERENCES projects(project_id) ON DELETE CASCADE
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_segments_chapter ON chapter_segments(chapter_id, sequence_order);",

    # ── chapter_failed_generations (LOADER_SPEC Loader 9) ──────────────
    # When the user discards a generation, archive it so the next fresh
    # generation can avoid the same direction. ``issues_detected`` is
    # populated lazily by FailureAnalyzer (LLM).
    """
    CREATE TABLE IF NOT EXISTS chapter_failed_generations (
        failed_gen_id TEXT PRIMARY KEY,
        chapter_id TEXT NOT NULL,
        project_id TEXT NOT NULL,
        content_snippet TEXT NOT NULL DEFAULT '',
        full_content TEXT NOT NULL DEFAULT '',
        rejected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        rejection_reason TEXT NOT NULL DEFAULT '',
        issues_detected TEXT NOT NULL DEFAULT '[]',
        FOREIGN KEY (chapter_id) REFERENCES chapters(chapter_id) ON DELETE CASCADE,
        FOREIGN KEY (project_id) REFERENCES projects(project_id) ON DELETE CASCADE
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_failed_gens_chapter ON chapter_failed_generations(chapter_id, rejected_at);",

    # ── skill_index (LOADER_SPEC Loader 14 prerequisite) ───────────────
    # DB mirror of the filesystem SkillRegistry, keyed by skill_id (=
    # filesystem skill name). Holds the per-skill embedding so the
    # prompt-context loader can rank skills against the chapter outline
    # without re-embedding on every call. SkillRegistry is still the
    # source of truth for which skills exist; this table is rebuilt
    # from it via ``skill_index.sync_from_registry``.
    """
    CREATE TABLE IF NOT EXISTS skill_index (
        skill_id TEXT PRIMARY KEY,
        display_name TEXT NOT NULL,
        description TEXT NOT NULL DEFAULT '',
        kind TEXT NOT NULL DEFAULT 'builtin'
            CHECK (kind IN ('builtin','learned')),
        body_snippet TEXT NOT NULL DEFAULT '',
        embedding_json TEXT NOT NULL DEFAULT '[]',
        embedding_text_hash TEXT NOT NULL DEFAULT '',
        embedding_model_key TEXT NOT NULL DEFAULT '',
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_skill_index_kind ON skill_index(kind);",

    # ── project_skill_pins (LOADER_SPEC Loader 14) ─────────────────────
    # User's per-project must-include skill set. Pins survive across
    # SkillRegistry rescans because skill_id is the stable filesystem
    # name; if the skill itself disappears from disk, sync_from_registry
    # cascades the pin away via the FK on skill_index.
    """
    CREATE TABLE IF NOT EXISTS project_skill_pins (
        project_id TEXT NOT NULL,
        skill_id TEXT NOT NULL,
        pinned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (project_id, skill_id),
        FOREIGN KEY (project_id) REFERENCES projects(project_id) ON DELETE CASCADE,
        FOREIGN KEY (skill_id) REFERENCES skill_index(skill_id) ON DELETE CASCADE
    );
    """,

    # ── character_snapshots (LOADER_SPEC Loader 5, Batch 5) ────────────
    # Snapshot-level character overrides, bound to specific chapters so
    # the prompt loader can render the "right" personality / speech /
    # Layer B / relation state for whichever chapter is being written.
    # Replaces the v0 ``characters.extra_json.dynamic_snapshots`` field
    # (one-shot migration moves any pre-existing data into this table).
    """
    CREATE TABLE IF NOT EXISTS character_snapshots (
        snapshot_id TEXT PRIMARY KEY,
        character_id TEXT NOT NULL,
        project_id TEXT NOT NULL,
        snapshot_order INTEGER NOT NULL,
        expected_chapter_range_start INTEGER,
        expected_chapter_range_end INTEGER,
        trigger_description TEXT NOT NULL DEFAULT '',
        personality_override TEXT NOT NULL DEFAULT '',
        speech_style_override TEXT NOT NULL DEFAULT '',
        alias TEXT NOT NULL DEFAULT '',
        layer_b_overrides_json TEXT NOT NULL DEFAULT '{}',
        relations_overrides_json TEXT NOT NULL DEFAULT '{}',
        other_changes TEXT NOT NULL DEFAULT '',
        bound_chapters_json TEXT NOT NULL DEFAULT '[]',
        transition_complete_chapter INTEGER,
        -- v3.1 角色卡·机制2: 转变态 3 档位（动摇 wavering / 试探 probing /
        -- 倾向 leaning），按角色故事线推进章节逐章设置: {"<章号>": "档位"}
        transition_stages_json TEXT NOT NULL DEFAULT '{}',
        bound_by TEXT NOT NULL DEFAULT 'user'
            CHECK (bound_by IN ('user','auto')),
        bound_at TIMESTAMP,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE (character_id, snapshot_order),
        FOREIGN KEY (character_id) REFERENCES characters(character_id) ON DELETE CASCADE,
        FOREIGN KEY (project_id) REFERENCES projects(project_id) ON DELETE CASCADE
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_snapshots_char_order ON character_snapshots(character_id, snapshot_order);",
    "CREATE INDEX IF NOT EXISTS idx_snapshots_proj ON character_snapshots(project_id);",

    # ── character_snapshot_reminders (LOADER_SPEC Loader 5) ────────────
    # Bookkeeping for the snapshot_reminder service so the same overdue
    # reminder isn't surfaced on every prompt build. The UNIQUE
    # constraint is the de-dup key — INSERT OR IGNORE keeps emits
    # idempotent.
    """
    CREATE TABLE IF NOT EXISTS character_snapshot_reminders (
        reminder_id TEXT PRIMARY KEY,
        snapshot_id TEXT NOT NULL,
        reminder_type TEXT NOT NULL
            CHECK (reminder_type IN
                ('snapshot_not_started','snapshot_transition_not_completed')),
        triggered_at_chapter INTEGER NOT NULL,
        user_acknowledged INTEGER NOT NULL DEFAULT 0,
        user_action TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE (snapshot_id, reminder_type, triggered_at_chapter),
        FOREIGN KEY (snapshot_id) REFERENCES character_snapshots(snapshot_id) ON DELETE CASCADE
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_reminders_snap ON character_snapshot_reminders(snapshot_id, reminder_type);",
]


# ── ALTER TABLE upgrade map for v1 -> v2 ───────────────────────────
# (column_name, DDL fragment). Applied via ``_ensure_chapter_v2_columns``
# below. Idempotent — checks PRAGMA table_info first.
_CHAPTERS_V2_COLUMNS = [
    ("synopsis",          "TEXT NOT NULL DEFAULT ''"),
    ("time_label",        "TEXT NOT NULL DEFAULT ''"),
    ("location",          "TEXT NOT NULL DEFAULT ''"),
    ("characters_json",   "TEXT NOT NULL DEFAULT '[]'"),
    ("pov_character",     "TEXT NOT NULL DEFAULT ''"),
    ("extra_json",        "TEXT NOT NULL DEFAULT '{}'"),
    # v2.2 InkOS audit gate (no CHECK at ALTER level — SQLite limitation —
    # so the constraint only applies on fresh CREATE. App layer enforces
    # the same enum.)
    ("audit_status",      "TEXT NOT NULL DEFAULT 'audit_pending'"),
    ("audit_issues_json", "TEXT NOT NULL DEFAULT '[]'"),
    # v3.0 LOADER_SPEC Loader 7: chapters carry not just characters but
    # locations / items / organizations the chapter puts on stage.
    # JSON envelope shape:
    #   {"characters":[...],"locations":[...],"items":[...],"organizations":[...]}
    ("on_stage_entities", "TEXT NOT NULL DEFAULT '{}'"),
    # v3.1 LOADER_SPEC user_special_requirements: per-chapter free-text
    # the user wants the Writer to honour for THIS chapter only.
    ("special_requirements", "TEXT NOT NULL DEFAULT ''"),
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

    # Backfill on_stage_entities for rows that pre-date the v3 column:
    # carry the legacy characters_json into the new envelope so the
    # loader always sees a populated structure. Skip rows that already
    # have a non-default value (someone wrote them explicitly).
    cur.execute("""
        UPDATE chapters
           SET on_stage_entities = json_object(
                   'characters',    json(COALESCE(NULLIF(characters_json,''), '[]')),
                   'locations',     json('[]'),
                   'items',         json('[]'),
                   'organizations', json('[]')
               )
         WHERE on_stage_entities IN ('', '{}')
    """)


_WORLDBOOK_V2_COLUMNS = [
    # Per-entry embedding for outline-based similarity injection.
    # Stored as JSON list[float]; empty list means "not yet computed".
    # text_hash lets the loader detect a stale embedding when title /
    # category / content was edited after the embedding was stored.
    ("embedding_json",       "TEXT NOT NULL DEFAULT '[]'"),
    ("embedding_text_hash",  "TEXT NOT NULL DEFAULT ''"),
    # EMBEDDING_SPEC Phase 2: which model produced this embedding.
    # Empty means "unknown / legacy" → loader treats it as model-key
    # mismatch and skips the row from cosine ranking until reindex.
    ("embedding_model_key",  "TEXT NOT NULL DEFAULT ''"),
]


# Roadmap 2026 Q2 Stage 1 Task 5: projects table gains platform +
# category columns so Loader 2 (platform_market) can look up the
# project's platform_profile from market extractor data. Older project
# DBs created before this addition silently get the columns added on
# next backend boot; Loader 2 already gracefully returns None when the
# columns are missing, so the migration is non-breaking either way.
_PROJECTS_MARKET_COLUMNS = [
    ("platform", "TEXT NOT NULL DEFAULT ''"),
    ("category", "TEXT NOT NULL DEFAULT ''"),
]


def _ensure_projects_market_columns(conn: sqlite3.Connection) -> None:
    """Add platform + category to an existing projects table.

    Both columns are nullable-by-default (empty string) so legacy
    projects don't break — Loader 2 / market extractor treat empty
    strings the same as missing columns: no profile lookup.
    """
    cur = conn.cursor()
    try:
        existing = {row[1] for row in cur.execute("PRAGMA table_info(projects)")}
    except sqlite3.OperationalError:
        return
    for col, ddl in _PROJECTS_MARKET_COLUMNS:
        if col not in existing:
            cur.execute(f"ALTER TABLE projects ADD COLUMN {col} {ddl}")
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_projects_platform_cat "
        "ON projects(platform, category)"
    )


def _ensure_skill_index_v2_columns(conn: sqlite3.Connection) -> None:
    """Add embedding_model_key to an existing skill_index table.

    EMBEDDING_SPEC Phase 2: every embedding-bearing table tracks the
    model that produced the stored vector so the loader can tell when
    a switch-model has invalidated the cache.
    """
    cur = conn.cursor()
    try:
        existing = {row[1] for row in cur.execute("PRAGMA table_info(skill_index)")}
    except sqlite3.OperationalError:
        return
    if "embedding_model_key" not in existing:
        cur.execute(
            "ALTER TABLE skill_index ADD COLUMN "
            "embedding_model_key TEXT NOT NULL DEFAULT ''"
        )


def _ensure_worldbook_v2_columns(conn: sqlite3.Connection) -> None:
    """Add embedding columns to an existing worldbook_entries table."""
    cur = conn.cursor()
    try:
        existing = {row[1] for row in cur.execute("PRAGMA table_info(worldbook_entries)")}
    except sqlite3.OperationalError:
        return  # table not yet created; CREATE in CREATION_DDL will carry the columns
    for col, ddl in _WORLDBOOK_V2_COLUMNS:
        if col not in existing:
            cur.execute(f"ALTER TABLE worldbook_entries ADD COLUMN {col} {ddl}")


# LOADER_SPEC Loader 8 (Batch 6): chapter_summaries gains is_anchor +
# title columns so the reader_memory loader can surface user-marked
# "key chapter" anchors and render summaries with their titles.
_CHAPTER_SUMMARIES_V3_COLUMNS = [
    ("is_anchor",    "INTEGER NOT NULL DEFAULT 0"),
    ("title",        "TEXT NOT NULL DEFAULT ''"),
    # Post-commit Phase 2: track which subsystem produced the summary
    # ('llm_auto' from chapter_summarizer, 'user_manual' from manual
    # editing) + which LLM model + when. ``llm_model='' && generated_at
    # IS NULL`` for legacy rows that predate this column.
    ("generated_by", "TEXT NOT NULL DEFAULT ''"),
    ("llm_model",    "TEXT NOT NULL DEFAULT ''"),
    ("generated_at", "TIMESTAMP"),
]


# Post-commit Phase 2: parallel additions to episodic_events so the
# event_extractor sub-task can attribute auto-generated events.
_EPISODIC_EVENTS_PC_COLUMNS = [
    ("generated_by", "TEXT NOT NULL DEFAULT ''"),
    ("llm_model",    "TEXT NOT NULL DEFAULT ''"),
]


def _ensure_episodic_events_pc_columns(conn: sqlite3.Connection) -> None:
    cur = conn.cursor()
    try:
        existing = {row[1] for row in cur.execute("PRAGMA table_info(episodic_events)")}
    except sqlite3.OperationalError:
        return
    for col, ddl in _EPISODIC_EVENTS_PC_COLUMNS:
        if col not in existing:
            cur.execute(f"ALTER TABLE episodic_events ADD COLUMN {col} {ddl}")


# v3.1 自学习 (spec 4.1): 来源章节 / 来源类型 columns on
# user_style_preferences for DBs created before this release.
_USER_PREFS_V31_COLUMNS = [
    ("source_chapters_json", "TEXT NOT NULL DEFAULT '[]'"),
    ("source_type",          "TEXT NOT NULL DEFAULT 'edit_inference'"),
]


def _ensure_user_prefs_v31_columns(conn: sqlite3.Connection) -> None:
    cur = conn.cursor()
    try:
        existing = {
            row[1]
            for row in cur.execute("PRAGMA table_info(user_style_preferences)")
        }
    except sqlite3.OperationalError:
        return
    for col, ddl in _USER_PREFS_V31_COLUMNS:
        if col not in existing:
            cur.execute(
                f"ALTER TABLE user_style_preferences ADD COLUMN {col} {ddl}"
            )


def _ensure_snapshot_v31_columns(conn: sqlite3.Connection) -> None:
    """v3.1 角色卡·机制2: 转变态 3 档位 column for pre-existing DBs."""
    cur = conn.cursor()
    try:
        existing = {
            row[1]
            for row in cur.execute("PRAGMA table_info(character_snapshots)")
        }
    except sqlite3.OperationalError:
        return
    if "transition_stages_json" not in existing:
        cur.execute(
            "ALTER TABLE character_snapshots ADD COLUMN "
            "transition_stages_json TEXT NOT NULL DEFAULT '{}'"
        )


def _ensure_chapter_summaries_v3_columns(conn: sqlite3.Connection) -> None:
    cur = conn.cursor()
    try:
        existing = {row[1] for row in cur.execute("PRAGMA table_info(chapter_summaries)")}
    except sqlite3.OperationalError:
        return
    for col, ddl in _CHAPTER_SUMMARIES_V3_COLUMNS:
        if col not in existing:
            cur.execute(f"ALTER TABLE chapter_summaries ADD COLUMN {col} {ddl}")
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_summaries_anchor "
        "ON chapter_summaries(project_id, is_anchor)"
    )


_BANNED_WORLDBOOK_CATEGORIES = {
    "角色", "character", "characters", "主角", "配角", "反派",
}


def _drop_v1_redundant_objects(conn: sqlite3.Connection) -> None:
    """Drop the tables / columns that the Truth File system supersedes.

    Idempotent — checks existence before dropping. SQLite supports
    ``DROP COLUMN`` since 3.35 (2021) and the project already depends
    on a recent enough Python (3.11) which ships with 3.40+.

    Order matters: drop indexes BEFORE dropping the columns they
    reference, else SQLite errors out.

    See docs/SCHEMA_REDESIGN.md for what each removal is replaced by.
    """
    cur = conn.cursor()

    # 1. permanent_facts → truth_current_state
    cur.execute("DROP INDEX IF EXISTS idx_perm_project")
    cur.execute("DROP TABLE IF EXISTS permanent_facts")

    # 2. episodic_events.foreshadow_status / foreshadow_target_chapter
    #    → pending_hooks / hook_events
    cur.execute("DROP INDEX IF EXISTS idx_episodic_foreshadow")
    cols = {row[1] for row in cur.execute("PRAGMA table_info(episodic_events)")}
    if "foreshadow_status" in cols:
        cur.execute("ALTER TABLE episodic_events DROP COLUMN foreshadow_status")
    if "foreshadow_target_chapter" in cols:
        cur.execute("ALTER TABLE episodic_events DROP COLUMN foreshadow_target_chapter")

    # 3. episodic_events.causality_json — v2.1 MEMORY_VS_TRUTH cleanup.
    #    Never read; only written as '{}'. Dropped to reclaim the column.
    if "causality_json" in cols:
        cur.execute("ALTER TABLE episodic_events DROP COLUMN causality_json")

    # 4. worldbook_entries: coerce banned 角色-shaped categories to '杂项'
    #    so the new CHECK constraint passes after this migration.
    #    The CHECK itself only applies to fresh CREATEs; existing tables
    #    keep their old schema. We coerce data here so any *new* writes
    #    via project_store hit the right enforcement.
    try:
        cur.execute(
            "UPDATE worldbook_entries SET category='杂项' WHERE category IN "
            "(" + ",".join(["?"] * len(_BANNED_WORLDBOOK_CATEGORIES)) + ")",
            tuple(_BANNED_WORLDBOOK_CATEGORIES),
        )
    except sqlite3.OperationalError:
        # Table not yet created on first call; the fresh CREATE will
        # carry the CHECK so the migration is a no-op.
        pass

    # 5. writing_knowledge — removed in v3.1 (loader + helpers + router
    #    all gone). Drop the table on existing DBs.
    cur.execute("DROP INDEX IF EXISTS idx_writing_knowledge_domain")
    cur.execute("DROP TABLE IF EXISTS writing_knowledge")


def ensure_creation_tables(conn: sqlite3.Connection) -> None:
    """Create all project tables (v2 schema), plus Truth File tables.

    Idempotent: safe to call on a fresh DB or one that already has the
    v1 schema. Adds v2-only columns / tables in-place AND drops the
    v1-only tables / columns that the Truth File system supersedes.
    """
    cur = conn.cursor()
    cur.execute("PRAGMA foreign_keys = ON;")
    for ddl in CREATION_DDL:
        cur.execute(ddl)
    _ensure_chapter_v2_columns(conn)
    _ensure_worldbook_v2_columns(conn)
    _ensure_skill_index_v2_columns(conn)
    _ensure_projects_market_columns(conn)
    _ensure_chapter_summaries_v3_columns(conn)
    _ensure_episodic_events_pc_columns(conn)
    _ensure_user_prefs_v31_columns(conn)
    _ensure_snapshot_v31_columns(conn)
    _drop_v1_redundant_objects(conn)
    conn.commit()

    from storage.truth_schema import ensure_truth_tables
    ensure_truth_tables(conn)

    from storage.post_commit_schema import ensure_post_commit_tables
    ensure_post_commit_tables(conn)

    from storage.market_extractor_schema import ensure_market_extractor_tables
    ensure_market_extractor_tables(conn)

    from storage.llm_outputs_schema import ensure_llm_outputs_table
    ensure_llm_outputs_table(conn)

    from storage.pipeline_session_schema import ensure_pipeline_session_table
    ensure_pipeline_session_table(conn)
