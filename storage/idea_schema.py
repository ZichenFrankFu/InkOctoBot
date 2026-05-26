"""Idea (inspiration) database schema.

The 灵感库 — free-text idea snippets (scenes, plot devices, character
designs, ...) that the 灵感搜索 page can store and similarity-search.
Independent of any single reference work, so it lives in its own
database file (``data/idea.db``).

Previously this table was co-located with the reference_works tables
inside ``novels.db``; the rename moves it out so the two surfaces are
not coupled at the file level.
"""
from __future__ import annotations

import sqlite3


_INSPIRATIONS = """
CREATE TABLE IF NOT EXISTS inspirations (
    id TEXT PRIMARY KEY,
    category TEXT NOT NULL DEFAULT 'other',
    title TEXT,
    content TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);"""

_INDEXES = """
CREATE INDEX IF NOT EXISTS idx_inspirations_category ON inspirations (category);
CREATE INDEX IF NOT EXISTS idx_inspirations_updated ON inspirations (updated_at);
"""

ALL_DDL = [_INSPIRATIONS, _INDEXES]

# Schema setup is process-global and idempotent.
_ensured_paths: set[str] = set()


def _conn_path(conn: sqlite3.Connection) -> str:
    try:
        for _seq, name, file in conn.execute("PRAGMA database_list"):
            if name == "main":
                return file or ":memory:"
    except Exception:
        pass
    return ":memory:"


def ensure_idea_tables(conn: sqlite3.Connection) -> None:
    """Create the inspirations table + indexes (idempotent)."""
    path = _conn_path(conn)
    if path != ":memory:" and path in _ensured_paths:
        return
    for ddl in ALL_DDL:
        conn.executescript(ddl)
    conn.commit()
    if path != ":memory:":
        _ensured_paths.add(path)
