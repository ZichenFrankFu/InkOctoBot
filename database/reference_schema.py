"""reference_schema.py — DDL matching README §5.2.2"""
from __future__ import annotations
import sqlite3

_REFERENCE_WORKS = """
CREATE TABLE IF NOT EXISTS reference_works (
    ref_id TEXT PRIMARY KEY, title TEXT NOT NULL, creator TEXT,
    media_type TEXT NOT NULL DEFAULT 'web_novel' CHECK (media_type IN ('web_novel','literature','poetry','film','anime','tv_series','other')),
    genre TEXT, tags_json TEXT,
    source TEXT NOT NULL DEFAULT 'manual' CHECK (source IN ('platform_crawl','file_upload','manual','novel_corpus')),
    platform TEXT, novel_uid INTEGER, file_path TEXT,
    user_rating INTEGER CHECK (user_rating BETWEEN 1 AND 5), user_summary TEXT,
    user_why_i_like TEXT, learning_dimensions_json TEXT,
    has_full_text INTEGER NOT NULL DEFAULT 0,
    preprocessing_status TEXT NOT NULL DEFAULT 'not_applicable' CHECK (preprocessing_status IN ('not_applicable','pending','processing','done','error')),
    style_fingerprint_json TEXT, narrative_structure_json TEXT,
    extracted_characters_json TEXT, rhythm_template_json TEXT,
    plot_outline_json TEXT, segments_json TEXT, settings_json TEXT,
    rhythm_json TEXT,
    serial_status TEXT CHECK (serial_status IN ('ongoing','completed','hiatus','unknown')),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);"""

_REFERENCE_ENTRIES = """
CREATE TABLE IF NOT EXISTS reference_entries (
    entry_id TEXT PRIMARY KEY, ref_id TEXT NOT NULL,
    entry_type TEXT NOT NULL DEFAULT 'other' CHECK (entry_type IN ('scene','character','worldbuilding','dialogue','technique','atmosphere','plot_structure','emotional_beat','hook','style_sample','other')),
    title TEXT, content TEXT,
    content_source TEXT DEFAULT 'user_written' CHECK (content_source IN ('original_text','user_written')),
    position_label TEXT, user_notes TEXT, learning_dimensions_json TEXT,
    user_rating INTEGER CHECK (user_rating BETWEEN 1 AND 5), tags_json TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (ref_id) REFERENCES reference_works (ref_id) ON DELETE CASCADE
);"""

_PROJECT_REFERENCE_LINKS = """
CREATE TABLE IF NOT EXISTS project_reference_links (
    link_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, ref_id TEXT NOT NULL,
    dimension TEXT NOT NULL CHECK (dimension IN ('world','character','plot','style','mood')),
    entry_ids_json TEXT, reference_character_name TEXT, notes TEXT,
    FOREIGN KEY (ref_id) REFERENCES reference_works (ref_id) ON DELETE CASCADE
);"""

# Per-work, per-level (L1/L2/L3) indexing progress so deep-indexing of
# multi-million-token works can be resumed across restarts.
_WORK_INDEX_PROGRESS = """
CREATE TABLE IF NOT EXISTS work_index_progress (
    ref_id TEXT NOT NULL,
    level TEXT NOT NULL,                  -- 'L1' | 'L2' | 'L3'
    backend TEXT NOT NULL,                -- embedding backend name
    done INTEGER NOT NULL DEFAULT 0,      -- chunks/items embedded so far
    total INTEGER NOT NULL DEFAULT 0,     -- expected total when known
    last_ordinal INTEGER NOT NULL DEFAULT -1,  -- last chunk ord persisted (L3)
    status TEXT NOT NULL DEFAULT 'pending', -- pending|running|done|error
    error TEXT,
    started_at TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (ref_id, level, backend),
    FOREIGN KEY (ref_id) REFERENCES reference_works (ref_id) ON DELETE CASCADE
);"""

ALL_DDL = [_REFERENCE_WORKS, _REFERENCE_ENTRIES, _PROJECT_REFERENCE_LINKS, _WORK_INDEX_PROGRESS]

def ensure_reference_tables(conn: sqlite3.Connection) -> None:
    for ddl in ALL_DDL: conn.executescript(ddl)
    # Lightweight migrations for additive columns on pre-existing DBs
    cols = {r[1] for r in conn.execute("PRAGMA table_info(reference_works)").fetchall()}
    if "plot_outline_json" not in cols:
        conn.execute("ALTER TABLE reference_works ADD COLUMN plot_outline_json TEXT")
    if "segments_json" not in cols:
        conn.execute("ALTER TABLE reference_works ADD COLUMN segments_json TEXT")
    if "settings_json" not in cols:
        conn.execute("ALTER TABLE reference_works ADD COLUMN settings_json TEXT")
    if "serial_status" not in cols:
        conn.execute("ALTER TABLE reference_works ADD COLUMN serial_status TEXT")
    if "rhythm_json" not in cols:
        conn.execute("ALTER TABLE reference_works ADD COLUMN rhythm_json TEXT")
    conn.commit()