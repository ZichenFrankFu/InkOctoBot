"""Centralized SQLite connection management.

Every DB connection opened through this module:
- Applies the same PRAGMAs (WAL mode, foreign keys, normal sync) so
  there's no drift between handlers
- Logs open / commit / rollback / retry / errors at appropriate levels
  so the DB layer is never a black box (closes GAP 6 of the
  observability audit — was silent retry-on-lock)
- Sets ``row_factory = sqlite3.Row`` so callers can use dict-style
  access without remembering to set it

Usage:

    from storage.connection import get_conn, run_with_retry

    with get_conn(db_path) as conn:
        rows = conn.execute("SELECT * FROM foo WHERE id=?", (fid,)).fetchall()

    # Or for write-heavy ops with lock contention:
    def _do_write(conn):
        conn.execute("INSERT INTO bar VALUES (?,?)", (a, b))
    run_with_retry(db_path, _do_write)

The 196+ existing ``sqlite3.connect(...)`` call sites in the codebase
can migrate to this incrementally; the legacy raw connections still
work, just without the unified logging.
"""
from __future__ import annotations

import logging
import sqlite3
import threading
import time
from contextlib import contextmanager
from typing import Any, Callable, Iterator

logger = logging.getLogger("inkoctobot.storage.connection")


# Per-path lock so write-heavy operations against the SAME file serialize
# at the Python level (SQLite's own locks handle the rest). Read-only ops
# don't need this — they can run concurrently.
_path_locks: dict[str, threading.Lock] = {}
_locks_lock = threading.Lock()


def _path_lock(db_path: str) -> threading.Lock:
    with _locks_lock:
        if db_path not in _path_locks:
            _path_locks[db_path] = threading.Lock()
        return _path_locks[db_path]


def _apply_pragmas(conn: sqlite3.Connection) -> None:
    """Apply the project's canonical PRAGMA set."""
    # WAL: concurrent readers + a single writer (best for our workload).
    # foreign_keys=ON: SQLite's default is OFF (!); we always want them on
    #                  so cascade deletes work and orphan rows are blocked.
    # synchronous=NORMAL: durable, ~3x faster than FULL; good for our use.
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.row_factory = sqlite3.Row


@contextmanager
def get_conn(
    db_path: str,
    *,
    timeout: float = 30.0,
    readonly: bool = False,
) -> Iterator[sqlite3.Connection]:
    """Open a connection with the canonical PRAGMAs.

    ``readonly=True`` skips the per-path lock and opens with
    ``check_same_thread=False`` so multiple readers can share. Writers
    should leave ``readonly=False`` (the default) to take the lock.
    """
    t0 = time.monotonic()
    logger.debug("db_conn open path=%s readonly=%s", db_path, readonly)
    lock = _path_lock(db_path) if not readonly else None
    if lock is not None:
        lock.acquire()
    try:
        conn = sqlite3.connect(
            db_path,
            timeout=timeout,
            check_same_thread=False,
        )
        _apply_pragmas(conn)
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            logger.exception("db_conn rollback path=%s", db_path)
            raise
        finally:
            conn.close()
            dt = (time.monotonic() - t0) * 1000
            logger.debug("db_conn close path=%s elapsed_ms=%d", db_path, int(dt))
    finally:
        if lock is not None:
            lock.release()


def run_with_retry(
    db_path: str,
    fn: Callable[[sqlite3.Connection], Any],
    *,
    max_retries: int = 5,
    base_sleep: float = 0.1,
    label: str = "",
) -> Any:
    """Run ``fn(conn)`` with backoff on ``database is locked`` errors.

    Each retry is logged at WARNING level with the attempt number; the
    final failure is logged at ERROR with full exc_info. Use ``label``
    to identify the operation in the log (e.g. ``"upsert_rank_snapshot"``).
    """
    last_err: Exception | None = None
    for attempt in range(max_retries):
        try:
            with get_conn(db_path) as conn:
                return fn(conn)
        except sqlite3.OperationalError as e:
            last_err = e
            msg = str(e).lower()
            if "locked" in msg or "busy" in msg:
                sleep_for = base_sleep * (2 ** attempt)
                logger.warning(
                    "db_retry op=%s attempt=%d/%d wait=%.2fs reason=%s",
                    label or "?", attempt + 1, max_retries, sleep_for, e,
                )
                time.sleep(sleep_for)
                continue
            # Non-lock OperationalError: don't retry.
            logger.exception("db_op_failed op=%s non-lock error", label or "?")
            raise
        except Exception:
            logger.exception("db_op_failed op=%s", label or "?")
            raise
    logger.error(
        "db_op_exhausted op=%s attempts=%d last_error=%s",
        label or "?", max_retries, last_err,
    )
    assert last_err is not None
    raise last_err


def log_db_action(action: str, **fields: Any) -> None:
    """Convenience: emit a one-line structured log for a DB action.

    Pattern: ``log_db_action("upsert_novel", platform="qidian", uid=42)``
    produces ``db_action op=upsert_novel platform=qidian uid=42``. Use
    sparingly — only on the meaningful state transitions, not every
    SELECT. INFO level.
    """
    pairs = " ".join(f"{k}={v!r}" if isinstance(v, str) else f"{k}={v}"
                     for k, v in fields.items())
    logger.info("db_action op=%s %s", action, pairs)
