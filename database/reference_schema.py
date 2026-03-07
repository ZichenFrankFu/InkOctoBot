"""
reference_schema.py — 参考作品数据库 DDL

精确对应 README §5.2.2 的 ER 图。
调用 ensure_reference_tables(conn) 追加到已有的 webnovel.db 中。
"""
from __future__ import annotations
import sqlite3
from pathlib import Path

# ---------- DDL ----------

_REFERENCE_WORKS = """
CREATE TABLE IF NOT EXISTS reference_works (
    ref_id                      TEXT PRIMARY KEY,
    title                       TEXT NOT NULL,
    creator                     TEXT,
    media_type                  TEXT NOT NULL DEFAULT 'web_novel'
        CHECK (media_type IN ('web_novel','literature','poetry','film','anime','tv_series','other')),
    genre                       TEXT,
    tags_json                   TEXT,                          -- JSON array
    source                      TEXT NOT NULL DEFAULT 'manual'
        CHECK (source IN ('platform_crawl','file_upload','manual')),
    platform                    TEXT,                          -- qidian | fanqie | null
    novel_uid                   INTEGER,                       -- 仅 platform_crawl
    file_path                   TEXT,                          -- 仅 file_upload
    user_rating                 INTEGER CHECK (user_rating BETWEEN 1 AND 5),
    user_summary                TEXT,
    user_why_i_like             TEXT,                          -- 审美倾向核心字段
    learning_dimensions_json    TEXT,                          -- JSON array of dimensions
    has_full_text               INTEGER NOT NULL DEFAULT 0,    -- boolean
    preprocessing_status        TEXT NOT NULL DEFAULT 'not_applicable'
        CHECK (preprocessing_status IN ('not_applicable','pending','processing','done','error')),
    style_fingerprint_json      TEXT,                          -- 仅全文作品
    narrative_structure_json    TEXT,
    extracted_characters_json   TEXT,
    rhythm_template_json        TEXT,
    created_at                  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at                  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

_REFERENCE_ENTRIES = """
CREATE TABLE IF NOT EXISTS reference_entries (
    entry_id                    TEXT PRIMARY KEY,
    ref_id                      TEXT NOT NULL,
    entry_type                  TEXT NOT NULL DEFAULT 'other'
        CHECK (entry_type IN (
            'scene','character','worldbuilding','dialogue','technique',
            'atmosphere','plot_structure','emotional_beat','hook','style_sample','other'
        )),
    title                       TEXT,
    content                     TEXT,
    content_source              TEXT DEFAULT 'user_written'
        CHECK (content_source IN ('original_text','user_written')),
    position_label              TEXT,                          -- 第3章 | S1E05 | 01:23:45 | Act 2
    user_notes                  TEXT,
    learning_dimensions_json    TEXT,                          -- JSON array, 条目级维度
    user_rating                 INTEGER CHECK (user_rating BETWEEN 1 AND 5),
    tags_json                   TEXT,
    created_at                  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (ref_id) REFERENCES reference_works (ref_id) ON DELETE CASCADE
);
"""

_PROJECT_REFERENCE_LINKS = """
CREATE TABLE IF NOT EXISTS project_reference_links (
    link_id                     TEXT PRIMARY KEY,
    project_id                  TEXT NOT NULL,
    ref_id                      TEXT NOT NULL,
    dimension                   TEXT NOT NULL
        CHECK (dimension IN ('world','character','plot','style','mood')),
    entry_ids_json              TEXT,                          -- JSON array, 可选精确到条目
    reference_character_name    TEXT,
    notes                       TEXT,
    FOREIGN KEY (ref_id) REFERENCES reference_works (ref_id) ON DELETE CASCADE
);
"""

ALL_DDL = [_REFERENCE_WORKS, _REFERENCE_ENTRIES, _PROJECT_REFERENCE_LINKS]


def ensure_reference_tables(conn: sqlite3.Connection) -> None:
    """幂等追加所有参考作品表。"""
    for ddl in ALL_DDL:
        conn.executescript(ddl)
    conn.commit()
