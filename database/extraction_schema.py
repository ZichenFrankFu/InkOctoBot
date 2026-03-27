"""extraction_schema.py — DDL for novel skill extraction pipeline tables."""
from __future__ import annotations

import sqlite3

_EXTRACTION_PROGRESS = """
CREATE TABLE IF NOT EXISTS extraction_progress (
    ref_id TEXT NOT NULL,
    phase TEXT NOT NULL
        CHECK (phase IN ('clean','chapter_extract','novel_aggregate','pattern_mine')),
    chapter_num INTEGER DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending','in_progress','completed','failed')),
    result_json TEXT,
    error_message TEXT,
    started_at TIMESTAMP,
    completed_at TIMESTAMP,
    PRIMARY KEY (ref_id, phase, chapter_num)
);"""

_NOVEL_METADATA = """
CREATE TABLE IF NOT EXISTS novel_metadata (
    ref_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    author TEXT,
    synopsis TEXT,
    status TEXT CHECK (status IN ('completed','serializing')),
    latest_chapter TEXT,
    total_chapters INTEGER,
    total_chars INTEGER,
    excluded_author_notes INTEGER DEFAULT 0,
    cleaned_dir TEXT,
    source_file TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);"""

_NOVEL_CHAPTERS = """
CREATE TABLE IF NOT EXISTS novel_chapters (
    ref_id TEXT NOT NULL,
    chapter_num INTEGER NOT NULL,
    original_title TEXT,
    file_path TEXT,
    char_count INTEGER,
    is_author_note INTEGER DEFAULT 0,
    PRIMARY KEY (ref_id, chapter_num)
);"""

_EXTRACTED_PATTERNS = """
CREATE TABLE IF NOT EXISTS extracted_patterns (
    pattern_id TEXT PRIMARY KEY,
    category TEXT NOT NULL
        CHECK (category IN ('writing_technique','chapter_design','story_arc')),
    subcategory TEXT,
    pattern_name TEXT NOT NULL,
    description TEXT,
    examples_json TEXT,
    frequency INTEGER DEFAULT 1,
    quality_score REAL,
    skill_emitted INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);"""

ALL_DDL = [_EXTRACTION_PROGRESS, _NOVEL_METADATA, _NOVEL_CHAPTERS, _EXTRACTED_PATTERNS]


def ensure_extraction_tables(conn: sqlite3.Connection) -> None:
    """Create all extraction-related tables if they don't exist."""
    for ddl in ALL_DDL:
        conn.executescript(ddl)
    conn.commit()
