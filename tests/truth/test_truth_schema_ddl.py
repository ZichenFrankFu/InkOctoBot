"""Smoke tests for storage.truth_schema DDL creation."""
from __future__ import annotations

import sqlite3

import pytest

from storage.project_schema import ensure_creation_tables
from storage.truth_schema import ensure_truth_tables, TRUTH_DDL


# All tables created by truth_schema (used to verify presence)
_EXPECTED_TRUTH_TABLES = {
    "truth_current_state",
    "character_ledger",
    "pending_hooks",
    "hook_events",
    "subplot_threads",
    "emotion_arcs",
    "character_relations",
    "truth_apply_log",
}


@pytest.fixture
def fresh_conn(tmp_path):
    db_path = tmp_path / "test_truth.db"
    conn = sqlite3.connect(str(db_path))
    yield conn
    conn.close()


def _list_tables(conn: sqlite3.Connection) -> set[str]:
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table';")
    return {row[0] for row in cur.fetchall()}


def test_ensure_truth_tables_creates_all_expected(fresh_conn):
    # truth tables FK to projects → bootstrap creation tables first
    ensure_creation_tables(fresh_conn)
    tables = _list_tables(fresh_conn)
    assert _EXPECTED_TRUTH_TABLES.issubset(tables), (
        f"Missing tables: {_EXPECTED_TRUTH_TABLES - tables}"
    )


def test_ensure_truth_tables_is_idempotent(fresh_conn):
    ensure_creation_tables(fresh_conn)
    ensure_truth_tables(fresh_conn)
    ensure_truth_tables(fresh_conn)  # second call must not raise
    tables = _list_tables(fresh_conn)
    assert _EXPECTED_TRUTH_TABLES.issubset(tables)


def test_creation_tables_wires_truth_tables(fresh_conn):
    """ensure_creation_tables alone should land truth tables too."""
    ensure_creation_tables(fresh_conn)
    tables = _list_tables(fresh_conn)
    assert _EXPECTED_TRUTH_TABLES.issubset(tables)


def test_pending_hooks_status_check_constraint(fresh_conn):
    ensure_creation_tables(fresh_conn)
    cur = fresh_conn.cursor()
    cur.execute(
        "INSERT INTO projects(project_id, title) VALUES(?, ?)",
        ("p1", "demo"),
    )
    fresh_conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        cur.execute(
            """INSERT INTO pending_hooks(hook_id, project_id, description,
                                          status, origin_chapter)
               VALUES(?, ?, ?, ?, ?)""",
            ("h1", "p1", "x", "bogus_status", 1),
        )
        fresh_conn.commit()


def test_ledger_operation_check_constraint(fresh_conn):
    ensure_creation_tables(fresh_conn)
    cur = fresh_conn.cursor()
    cur.execute("INSERT INTO projects(project_id, title) VALUES(?, ?)", ("p1", "x"))
    fresh_conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        cur.execute(
            """INSERT INTO character_ledger(ledger_id, project_id,
                  character_name, chapter_num, category, key,
                  operation, delta) VALUES(?, ?, ?, ?, ?, ?, ?, ?)""",
            ("l1", "p1", "张远", 1, "resource", "灵气", "divide", -10),
        )
        fresh_conn.commit()


def test_relation_unique_per_pair(fresh_conn):
    ensure_creation_tables(fresh_conn)
    cur = fresh_conn.cursor()
    cur.execute("INSERT INTO projects(project_id, title) VALUES(?, ?)", ("p1", "x"))
    cur.execute(
        """INSERT INTO character_relations(rel_id, project_id, character_a,
              character_b, relation_type, sentiment_score, trust_level)
           VALUES(?, ?, ?, ?, ?, ?, ?)""",
        ("r1", "p1", "A", "B", "盟友", 70, 70),
    )
    fresh_conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        cur.execute(
            """INSERT INTO character_relations(rel_id, project_id, character_a,
                  character_b, relation_type, sentiment_score, trust_level)
               VALUES(?, ?, ?, ?, ?, ?, ?)""",
            ("r2", "p1", "A", "B", "敌对", -60, 5),
        )
        fresh_conn.commit()


def test_apply_log_unique_per_hash(fresh_conn):
    ensure_creation_tables(fresh_conn)
    cur = fresh_conn.cursor()
    cur.execute(
        """INSERT INTO truth_apply_log(apply_id, project_id, chapter_num,
              deltas_hash) VALUES(?, ?, ?, ?)""",
        ("a1", "p1", 5, "hashabc"),
    )
    fresh_conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        cur.execute(
            """INSERT INTO truth_apply_log(apply_id, project_id, chapter_num,
                  deltas_hash) VALUES(?, ?, ?, ?)""",
            ("a2", "p1", 5, "hashabc"),
        )
        fresh_conn.commit()
