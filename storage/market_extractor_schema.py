"""Market-extractor schema — 6 tables + indexes.

All tables live in the main project DB (``data/novels.db``) so they
co-locate with ``projects`` for FK consistency on
``platform_profiles``.

``ensure_market_extractor_tables(conn)`` is idempotent — safe to
call on a fresh DB or one that's already initialized.
"""
from __future__ import annotations

import sqlite3


_DDL: tuple[str, ...] = (
    # ── 1. representative_works_pool ──
    """
    CREATE TABLE IF NOT EXISTS representative_works_pool (
        work_id                    TEXT PRIMARY KEY,
        platform                   TEXT NOT NULL,
        category                   TEXT NOT NULL,
        source_db_novel_id         TEXT,
        rank_score                 REAL,
        collection_count           INTEGER DEFAULT 0,
        comment_count              INTEGER DEFAULT 0,
        monthly_ticket             INTEGER DEFAULT 0,
        word_count                 INTEGER DEFAULT 0,
        last_updated_at            TIMESTAMP,
        selected_at                TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        selected_for_extraction    INTEGER NOT NULL DEFAULT 1,
        selection_round            INTEGER DEFAULT 1,
        is_holdout                 INTEGER NOT NULL DEFAULT 0,
        selection_reason           TEXT DEFAULT ''
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_rwp_platform_cat "
    "ON representative_works_pool(platform, category)",
    "CREATE INDEX IF NOT EXISTS idx_rwp_selected "
    "ON representative_works_pool(selected_for_extraction, is_holdout)",

    # ── 2. chapter_features ──
    """
    CREATE TABLE IF NOT EXISTS chapter_features (
        feature_id                              TEXT PRIMARY KEY,
        work_id                                 TEXT NOT NULL,
        chapter_num                             INTEGER NOT NULL,
        word_count                              INTEGER,
        paragraph_count                         INTEGER,
        dialogue_ratio                          REAL,
        avg_sentence_length                     REAL,
        short_sentence_ratio                    REAL,
        long_sentence_ratio                     REAL,
        adjective_density                       REAL,
        verb_density                            REAL,
        adverb_density                          REAL,
        metaphor_density                        REAL,
        onomatopoeia_count                      INTEGER,
        exclamation_density                     REAL,
        question_density                        REAL,
        ellipsis_density                        REAL,
        dash_count                              INTEGER,
        quotation_density                       REAL,
        halfwidth_ratio                         REAL,
        paragraph_length_p25                    INTEGER,
        paragraph_length_p50                    INTEGER,
        paragraph_length_p75                    INTEGER,
        paragraph_length_p95                    INTEGER,
        single_sentence_paragraph_ratio         REAL,
        dialogue_paragraph_ratio                REAL,
        description_paragraph_ratio             REAL,
        dialogue_tag_diversity                  REAL,
        monologue_ratio                         REAL,
        avg_dialogue_turns                      REAL,
        ttr                                     REAL,
        idiom_density                           REAL,
        classical_word_density                  REAL,
        internet_slang_density                  REAL,
        top_50_words_json                       TEXT,
        parallelism_count                       INTEGER,
        personification_count                   INTEGER,
        rhetorical_question_count               INTEGER,
        repetition_pattern_count                INTEGER,
        tfidf_top_words_json                    TEXT,
        chapter_title_length                    INTEGER,
        first_sentence_type                     TEXT,
        first_sentence_length                   INTEGER,
        first_paragraph_length                  INTEGER,
        last_sentence_length                    INTEGER,
        last_200_chars_emotion                  TEXT,
        scene_change_count                      INTEGER,
        noun_density                            REAL,
        verb_density_v2                         REAL,
        adjective_density_v2                    REAL,
        pace_ratio_json                         TEXT,
        -- LLM 15 fields
        protagonist_first_appearance_chapter    INTEGER,
        protagonist_first_appearance_position   TEXT,
        protagonist_first_appearance_context    TEXT,
        protagonist_strength_position           TEXT,
        protagonist_personality_json            TEXT,
        protagonist_appearance_detail_level     TEXT,
        protagonist_age_group                   TEXT,
        protagonist_contrast_point              TEXT,
        protagonist_cheat_type                  TEXT,
        protagonist_cheat_source                TEXT,
        protagonist_cheat_revealed_chapter      INTEGER,
        protagonist_cheat_description           TEXT,
        protagonist_agency                      TEXT,
        protagonist_first_decision_chapter      INTEGER,
        drive_type                              TEXT,
        drive_urgency                           TEXT,
        drive_object                            TEXT,
        drive_method                            TEXT,
        drive_description                       TEXT,
        social_network_json                     TEXT,
        key_relationships_json                  TEXT,
        focus_mode                              TEXT,
        character_count_in_5ch                  INTEGER,
        antagonist_first_chapter                INTEGER,
        antagonist_type                         TEXT,
        worldview_power_system                  TEXT,
        worldview_time_space                    TEXT,
        worldview_hybrid_type                   TEXT,
        worldview_novelty_level                 TEXT,
        setting_word_ratio                      REAL,
        setting_unfold_style                    TEXT,
        setting_density_first_chapter           TEXT,
        core_setting_revealed                   TEXT,
        setting_contrast_point                  TEXT,
        opening_hook_type                       TEXT,
        opening_hook_description                TEXT,
        opening_hook_trigger_position           TEXT,
        has_early_face_slap                     INTEGER,
        first_face_slap_chapter                 INTEGER,
        pleasure_point_type                     TEXT,
        pleasure_point_density                  INTEGER,
        writing_style_keywords_json             TEXT,
        representative_sentences_json           TEXT,
        emotional_tone                          TEXT,
        emotional_volatility                    TEXT,
        darkness_level                          TEXT,
        info_disclosure_strategy                TEXT,
        early_foreshadows_json                  TEXT,
        revealed_vs_planted_ratio               REAL,
        first_arc_concept                       TEXT,
        first_arc_locked_chapter                INTEGER,
        chapter_end_hook_type                   TEXT,
        chapter_end_hook_strength               TEXT,
        extracted_at                            TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        llm_model_used                          TEXT,
        extraction_cost_usd                     REAL DEFAULT 0,
        UNIQUE(work_id, chapter_num)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_cf_work "
    "ON chapter_features(work_id, chapter_num)",

    # ── 3. work_neologisms (single-work scope) ──
    """
    CREATE TABLE IF NOT EXISTS work_neologisms (
        neologism_id                TEXT PRIMARY KEY,
        work_id                     TEXT NOT NULL,
        term                        TEXT NOT NULL,
        neologism_type              TEXT,
        first_chapter               INTEGER,
        first_position              TEXT,
        frequency_in_5_chapters     INTEGER DEFAULT 0,
        definition_hint             TEXT,
        context_samples_json        TEXT,
        extracted_at                TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        llm_model_used              TEXT,
        UNIQUE(work_id, term)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_wn_work ON work_neologisms(work_id)",
    "CREATE INDEX IF NOT EXISTS idx_wn_type ON work_neologisms(neologism_type)",
    "CREATE INDEX IF NOT EXISTS idx_wn_term ON work_neologisms(term)",

    # ── 4. work_aggregated_features ──
    """
    CREATE TABLE IF NOT EXISTS work_aggregated_features (
        work_id                         TEXT PRIMARY KEY,
        platform                        TEXT NOT NULL,
        category                        TEXT NOT NULL,
        avg_chapter_word_count          REAL,
        median_chapter_word_count       REAL,
        avg_dialogue_ratio              REAL,
        avg_paragraph_count             REAL,
        dominant_emotional_tone         TEXT,
        dominant_writing_style          TEXT,
        opening_pattern                 TEXT,
        opening_hook_type               TEXT,
        opening_hook_description        TEXT,
        signature_devices_used_json     TEXT,
        node_positions_json             TEXT,
        work_top_words_json             TEXT,
        neologism_count                 INTEGER DEFAULT 0,
        neologism_count_by_type_json    TEXT,
        aggregated_at                   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_waf_platcat "
    "ON work_aggregated_features(platform, category)",

    # ── 5. category_aggregated_stats ──
    """
    CREATE TABLE IF NOT EXISTS category_aggregated_stats (
        stats_id                                    TEXT PRIMARY KEY,
        platform                                    TEXT NOT NULL,
        category                                    TEXT NOT NULL,
        source_works_count                          INTEGER,
        source_works_ids_json                       TEXT,
        opening_hook_type_distribution_json         TEXT,
        protagonist_cheat_type_distribution_json    TEXT,
        protagonist_agency_distribution_json        TEXT,
        worldview_type_distribution_json            TEXT,
        writing_style_distribution_json             TEXT,
        emotional_tone_distribution_json            TEXT,
        info_disclosure_distribution_json           TEXT,
        chapter_word_count_stats_json               TEXT,
        dialogue_ratio_stats_json                   TEXT,
        setting_word_ratio_stats_json               TEXT,
        first_breakthrough_chapter_median           INTEGER,
        antagonist_first_chapter_median             INTEGER,
        first_face_slap_chapter_median              INTEGER,
        genre_vocabulary_top_json                   TEXT,
        avg_neologisms_per_work                     REAL,
        neologism_type_distribution_json            TEXT,
        chapter_end_hook_type_distribution_json     TEXT,
        aggregated_at                               TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_cas_platcat "
    "ON category_aggregated_stats(platform, category, aggregated_at)",

    # ── 6. platform_profiles ──
    """
    CREATE TABLE IF NOT EXISTS platform_profiles (
        profile_id                       TEXT PRIMARY KEY,
        platform                         TEXT NOT NULL,
        category                         TEXT NOT NULL,
        profile_version                  INTEGER NOT NULL DEFAULT 1,
        source_works_count               INTEGER,
        source_works_ids_json            TEXT,
        extraction_started_at            TIMESTAMP,
        extraction_completed_at          TIMESTAMP,
        extraction_cost_usd              REAL DEFAULT 0,
        profile_summary                  TEXT,
        recommended_openings_json        TEXT,
        style_baseline                   TEXT,
        signature_devices_description    TEXT,
        pacing_guidance                  TEXT,
        loader_payload                   TEXT,
        loader_token_estimate            INTEGER,
        holdout_similarity_score         REAL,
        -- spec 2.1.3.2 高级特征提取：行文风格七组 (A1-G2) 结构化结果
        -- 与生造词 Step2（专有名词/人名/地名常见模式与常见字）。
        style_dimensions_json            TEXT,
        neologism_step2_json             TEXT,
        confidence_label                 TEXT,
        valid_from                       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        valid_until                      TIMESTAMP,
        superseded_by_profile_id         TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_pp_active "
    "ON platform_profiles(platform, category, valid_until)",

    # ── 7. platform_extraction_jobs ──
    """
    CREATE TABLE IF NOT EXISTS platform_extraction_jobs (
        job_id                  TEXT PRIMARY KEY,
        platform                TEXT NOT NULL,
        category                TEXT NOT NULL,
        state                   TEXT NOT NULL DEFAULT 'queued'
                                  CHECK (state IN (
                                      'queued', 'running_phase_1',
                                      'running_phase_2', 'running_phase_3',
                                      'running_phase_4', 'running_phase_5',
                                      'completed', 'failed', 'cancelled'
                                  )),
        progress_phase          TEXT,
        progress_pct            REAL DEFAULT 0,
        started_at              TIMESTAMP,
        completed_at            TIMESTAMP,
        works_count             INTEGER DEFAULT 0,
        chapters_processed      INTEGER DEFAULT 0,
        local_llm_calls         INTEGER DEFAULT 0,
        cloud_llm_calls         INTEGER DEFAULT 0,
        estimated_cost_usd      REAL DEFAULT 0,
        current_work_id         TEXT,
        errors_json             TEXT,
        created_profile_id      TEXT,
        created_at              TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_pej_state "
    "ON platform_extraction_jobs(state, created_at)",

    # ── 8. person_name_library （人名库；全名为权威记录，姓/名为派生字段）──
    """
    CREATE TABLE IF NOT EXISTS person_name_library (
        name_id             TEXT PRIMARY KEY,
        full_name           TEXT NOT NULL UNIQUE,     -- 权威记录
        surname             TEXT DEFAULT '',          -- 派生：姓（可重算）
        given_name          TEXT DEFAULT '',          -- 派生：名（可重算）
        surname_kind        TEXT DEFAULT 'unknown',   -- single|compound|unknown
        name_length         INTEGER DEFAULT 0,
        is_compound_surname INTEGER DEFAULT 0,        -- 复姓标记
        is_single_given     INTEGER DEFAULT 0,        -- 单名标记
        is_nonstandard      INTEGER DEFAULT 0,        -- 昵称/异常名 → 不进规律统计
        nonstandard_reason  TEXT DEFAULT '',
        source              TEXT DEFAULT '',          -- seed|ltp_ner|jieba_nr|user
        source_work_id      TEXT DEFAULT '',          -- 来源作品
        source_work_title   TEXT DEFAULT '',
        source_platform     TEXT DEFAULT '',
        source_category     TEXT DEFAULT '',
        source_work_rank    INTEGER,                  -- 来源作品排名
        source_work_heat    REAL,                     -- 来源作品热度
        book_df             INTEGER DEFAULT 0,        -- 去重书数 DF（按 book 不按 snapshot）
        example_sentence    TEXT DEFAULT '',          -- 该名出现的一句例句（来源作品）
        name_kind           TEXT DEFAULT 'chinese',   -- chinese|japanese|western|nickname
        alias_of            TEXT DEFAULT '',          -- 昵称→本名（如 小明→刘明）；可空
        first_seen_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_pnl_surname ON person_name_library(surname)",
    "CREATE INDEX IF NOT EXISTS idx_pnl_df ON person_name_library(book_df)",
    "CREATE INDEX IF NOT EXISTS idx_pnl_cat ON person_name_library(source_category)",

    # ── 9. name_extraction_state （NER 按 book 去重的处理台账）──
    """
    CREATE TABLE IF NOT EXISTS name_extraction_state (
        novel_uid        TEXT PRIMARY KEY,
        content_hash     TEXT DEFAULT '',   -- 开篇内容指纹；变则视为新书重跑
        method           TEXT DEFAULT '',   -- ltp_gpu|ltp_cpu|jieba|skipped
        names_found      INTEGER DEFAULT 0,
        processed_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
)


# Columns added after the table first shipped — ALTER in for existing DBs.
_PLATFORM_PROFILE_MIGRATIONS: tuple[tuple[str, str], ...] = (
    ("style_dimensions_json", "TEXT"),
    ("neologism_step2_json", "TEXT"),
)
_REP_POOL_MIGRATIONS: tuple[tuple[str, str], ...] = (
    ("selection_reason", "TEXT DEFAULT ''"),
)
_PNL_MIGRATIONS: tuple[tuple[str, str], ...] = (
    ("example_sentence", "TEXT DEFAULT ''"),
    ("name_kind", "TEXT DEFAULT 'chinese'"),
    ("alias_of", "TEXT DEFAULT ''"),
)


def ensure_market_extractor_tables(conn: sqlite3.Connection) -> None:
    """Create all 7 market-extractor tables + indexes. Idempotent."""
    cur = conn.cursor()
    for ddl in _DDL:
        cur.execute(ddl)
    # Idempotent column adds for DBs created before these columns existed.
    def _migrate(table: str, migrations: tuple[tuple[str, str], ...]) -> None:
        cols = {r[1] for r in cur.execute(f"PRAGMA table_info({table})").fetchall()}
        for col, coltype in migrations:
            if col not in cols:
                cur.execute(f"ALTER TABLE {table} ADD COLUMN {col} {coltype}")

    _migrate("platform_profiles", _PLATFORM_PROFILE_MIGRATIONS)
    _migrate("representative_works_pool", _REP_POOL_MIGRATIONS)
    _migrate("person_name_library", _PNL_MIGRATIONS)
    conn.commit()
