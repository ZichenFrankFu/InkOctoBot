"""Tests for storage.connection — the centralized DB gateway.

Covers PRAGMA application, commit/rollback semantics, and the retry
logging that closes observability GAP 6.
"""
from __future__ import annotations

import logging
import sqlite3
import time

import pytest

from storage.connection import get_conn, run_with_retry


# ── PRAGMA application ───────────────────────────────────────────


def test_get_conn_applies_canonical_pragmas(tmp_path):
    db = str(tmp_path / "t.db")
    with get_conn(db) as conn:
        journal = conn.execute("PRAGMA journal_mode").fetchone()[0]
        fk = conn.execute("PRAGMA foreign_keys").fetchone()[0]
        sync = conn.execute("PRAGMA synchronous").fetchone()[0]
    assert journal.lower() == "wal"
    assert fk == 1  # ON
    # synchronous=NORMAL maps to integer 1
    assert sync == 1


def test_row_factory_set_to_row(tmp_path):
    db = str(tmp_path / "t.db")
    with get_conn(db) as conn:
        conn.execute("CREATE TABLE t (x INT, y TEXT)")
        conn.execute("INSERT INTO t VALUES (1, 'a')")
        row = conn.execute("SELECT x, y FROM t").fetchone()
    assert row["x"] == 1
    assert row["y"] == "a"


# ── Transaction semantics ────────────────────────────────────────


def test_commits_on_success(tmp_path):
    db = str(tmp_path / "t.db")
    with get_conn(db) as conn:
        conn.execute("CREATE TABLE t (x INT)")
        conn.execute("INSERT INTO t VALUES (42)")
    # Re-open and check value persisted
    with get_conn(db, readonly=True) as conn:
        assert conn.execute("SELECT x FROM t").fetchone()["x"] == 42


def test_rollback_on_exception(tmp_path):
    db = str(tmp_path / "t.db")
    # Set up
    with get_conn(db) as conn:
        conn.execute("CREATE TABLE t (x INT)")

    with pytest.raises(RuntimeError):
        with get_conn(db) as conn:
            conn.execute("INSERT INTO t VALUES (1)")
            raise RuntimeError("boom")

    # Insert was rolled back
    with get_conn(db, readonly=True) as conn:
        rows = conn.execute("SELECT COUNT(*) AS n FROM t").fetchone()
        assert rows["n"] == 0


# ── run_with_retry — GAP 6 logging ───────────────────────────────


def test_run_with_retry_logs_each_retry(tmp_path, caplog):
    """Lock-contention retries must surface in the log so the operator
    can see what's happening when DB ops slow down."""
    db = str(tmp_path / "t.db")
    with get_conn(db) as conn:
        conn.execute("CREATE TABLE t (x INT)")

    call_count = {"n": 0}

    def flaky(conn):
        call_count["n"] += 1
        if call_count["n"] < 3:
            raise sqlite3.OperationalError("database is locked")
        conn.execute("INSERT INTO t VALUES (?)", (call_count["n"],))
        return call_count["n"]

    with caplog.at_level(logging.WARNING, logger="inkoctobot.storage.connection"):
        result = run_with_retry(db, flaky, max_retries=5,
                                 base_sleep=0.001, label="test_op")
    assert result == 3
    # Two retries should have produced two WARNING lines
    retry_logs = [r for r in caplog.records if "db_retry" in r.message]
    assert len(retry_logs) == 2
    assert all("op=test_op" in r.message for r in retry_logs)


def test_run_with_retry_logs_exhaustion(tmp_path, caplog):
    """When every attempt fails, the final ERROR log carries the count
    and last error so the cause is clear without reading code."""
    db = str(tmp_path / "t.db")

    def always_locked(conn):
        raise sqlite3.OperationalError("database is locked")

    with caplog.at_level(logging.ERROR, logger="inkoctobot.storage.connection"):
        with pytest.raises(sqlite3.OperationalError):
            run_with_retry(db, always_locked, max_retries=3,
                            base_sleep=0.001, label="never_succeeds")
    exhausted = [r for r in caplog.records if "db_op_exhausted" in r.message]
    assert len(exhausted) == 1
    assert "attempts=3" in exhausted[0].message
    assert "op=never_succeeds" in exhausted[0].message


def test_run_with_retry_does_not_retry_non_lock_errors(tmp_path, caplog):
    """A SQL syntax error should fail fast — not retry 5 times."""
    db = str(tmp_path / "t.db")

    def bad_sql(conn):
        conn.execute("SELECT FROM nowhere")  # syntax error

    with caplog.at_level(logging.ERROR, logger="inkoctobot.storage.connection"):
        with pytest.raises(sqlite3.OperationalError):
            run_with_retry(db, bad_sql, max_retries=5,
                            base_sleep=0.001, label="bad_query")
    # Should have produced exactly one db_op_failed (not 5 retries)
    failed = [r for r in caplog.records if "db_op_failed" in r.message]
    assert len(failed) == 1
