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
    project_id TEXT,
    category TEXT NOT NULL DEFAULT 'other',
    title TEXT,
    content TEXT NOT NULL DEFAULT '',
    tags_json TEXT NOT NULL DEFAULT '[]',
    embedding_json TEXT NOT NULL DEFAULT '[]',
    embedding_text_hash TEXT NOT NULL DEFAULT '',
    embedding_model_key TEXT NOT NULL DEFAULT '',
    used_in_chapters_json TEXT NOT NULL DEFAULT '[]',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);"""

_INDEXES = """
CREATE INDEX IF NOT EXISTS idx_inspirations_category ON inspirations (category);
CREATE INDEX IF NOT EXISTS idx_inspirations_updated ON inspirations (updated_at);
CREATE INDEX IF NOT EXISTS idx_inspirations_project ON inspirations (project_id);
"""

# v3 LOADER_SPEC Loader 4: project-scoped lookup + embedding-based
# similarity ranking + used-in-chapter exclusion. Existing rows have
# project_id=NULL ("global") and remain visible to every project.
_INSPIRATIONS_V3_COLUMNS = [
    ("project_id",              "TEXT"),
    ("tags_json",               "TEXT NOT NULL DEFAULT '[]'"),
    ("embedding_json",          "TEXT NOT NULL DEFAULT '[]'"),
    ("embedding_text_hash",     "TEXT NOT NULL DEFAULT ''"),
    ("embedding_model_key",     "TEXT NOT NULL DEFAULT ''"),
    ("used_in_chapters_json",   "TEXT NOT NULL DEFAULT '[]'"),
]


def _ensure_inspirations_v3_columns(conn: sqlite3.Connection) -> None:
    """Idempotently add v3 columns to a pre-existing inspirations table."""
    cur = conn.cursor()
    try:
        existing = {row[1] for row in cur.execute("PRAGMA table_info(inspirations)")}
    except sqlite3.OperationalError:
        return  # table not yet created; CREATE in ALL_DDL handles it
    for col, ddl in _INSPIRATIONS_V3_COLUMNS:
        if col not in existing:
            cur.execute(f"ALTER TABLE inspirations ADD COLUMN {col} {ddl}")


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
    """Create the inspirations table + indexes (idempotent).

    Column migration must run BEFORE index creation, because
    ``idx_inspirations_project`` references ``project_id`` — a column
    that's only present on v3+ tables. On a pre-v3 DB the CREATE
    TABLE is a no-op (table already exists without the column), so
    the column has to be added before any index referencing it can
    succeed."""
    path = _conn_path(conn)
    if path != ":memory:" and path in _ensured_paths:
        return
    # 1. Create-if-missing (schema-fresh path).
    conn.executescript(_INSPIRATIONS)
    # 2. Add v3 columns to a pre-existing table — must run before
    #    any index referencing them is built.
    _ensure_inspirations_v3_columns(conn)
    # 3. Now the indexes can safely reference project_id.
    conn.executescript(_INDEXES)
    conn.commit()
    if path != ":memory:":
        _ensured_paths.add(path)
