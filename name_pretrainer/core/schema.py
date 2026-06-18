"""Self-contained schema for the standalone name pre-trainer —— only the two
tables the name library needs (person_name_library + name_extraction_state),
mirrored from InkOctoBot's market_extractor_schema. Idempotent.
"""
from __future__ import annotations

import sqlite3

_DDL: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS person_name_library (
        name_id             TEXT PRIMARY KEY,
        full_name           TEXT NOT NULL UNIQUE,
        surname             TEXT DEFAULT '',
        given_name          TEXT DEFAULT '',
        surname_kind        TEXT DEFAULT 'unknown',
        name_length         INTEGER DEFAULT 0,
        is_compound_surname INTEGER DEFAULT 0,
        is_single_given     INTEGER DEFAULT 0,
        is_nonstandard      INTEGER DEFAULT 0,
        nonstandard_reason  TEXT DEFAULT '',
        source              TEXT DEFAULT '',
        source_work_id      TEXT DEFAULT '',
        source_work_title   TEXT DEFAULT '',
        source_platform     TEXT DEFAULT '',
        source_category     TEXT DEFAULT '',
        source_work_rank    INTEGER,
        source_work_heat    REAL,
        book_df             INTEGER DEFAULT 0,
        example_sentence    TEXT DEFAULT '',
        name_kind           TEXT DEFAULT 'chinese',
        gender              TEXT DEFAULT '',
        alias_of            TEXT DEFAULT '',
        first_seen_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_pnl_surname ON person_name_library(surname)",
    "CREATE INDEX IF NOT EXISTS idx_pnl_cat ON person_name_library(source_category)",
    "CREATE INDEX IF NOT EXISTS idx_pnl_kind ON person_name_library(name_kind)",
    """
    CREATE TABLE IF NOT EXISTS name_extraction_state (
        novel_uid        TEXT PRIMARY KEY,
        content_hash     TEXT DEFAULT '',
        method           TEXT DEFAULT '',
        names_found      INTEGER DEFAULT 0,
        processed_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
)

_PNL_MIGRATIONS: tuple[tuple[str, str], ...] = (
    ("example_sentence", "TEXT DEFAULT ''"),
    ("name_kind", "TEXT DEFAULT 'chinese'"),
    ("alias_of", "TEXT DEFAULT ''"),
    ("gender", "TEXT DEFAULT ''"),
)


def ensure_name_tables(conn: sqlite3.Connection) -> None:
    cur = conn.cursor()
    for ddl in _DDL:
        cur.execute(ddl)
    cols = {r[1] for r in cur.execute("PRAGMA table_info(person_name_library)").fetchall()}
    for col, coltype in _PNL_MIGRATIONS:
        if col not in cols:
            cur.execute(f"ALTER TABLE person_name_library ADD COLUMN {col} {coltype}")
    conn.commit()
