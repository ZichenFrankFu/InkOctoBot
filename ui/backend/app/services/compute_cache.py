"""Server-side compute cache with single-flight background execution.

The urgent fix for thread-pool exhaustion (恶性 bug: 多次切换页面后全
站无限加载): heavy sync endpoints (pandas trend analysis, opening-NLP
over hundreds of chapters) used to run INSIDE the request thread —
every page visit burned an anyio worker for seconds-to-minutes, and
once the pool drained every endpoint hung. This module flips them to:

- responses are ALWAYS instant: cached payload (with ``stale`` flag
  when the source DB changed) or ``{state: 'computing'}``
- the actual computation runs on ONE dedicated background thread per
  cache key (single-flight — N page visits can't stack N pandas runs)
- results persist in the project DB so the last finished analysis
  survives restarts (懒加载 + 保留上次结果 + 数据更新提醒)
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from typing import Any, Callable

logger = logging.getLogger("inkoctobot.services.compute_cache")

_lock = threading.Lock()
_in_progress: dict[str, float] = {}     # cache_key -> started_at
_errors: dict[str, str] = {}            # cache_key -> last error

# Stale in-progress guard: if a worker died without clearing its flag
# (process restart leaves no flag; this covers in-process crashes),
# allow a new run after this many seconds.
_IN_PROGRESS_TTL = 15 * 60


def _ensure_table(con: sqlite3.Connection) -> None:
    con.execute(
        """CREATE TABLE IF NOT EXISTS compute_cache (
               cache_key TEXT PRIMARY KEY,
               payload_json TEXT NOT NULL,
               version_key TEXT NOT NULL DEFAULT '',
               updated_at REAL NOT NULL
           )"""
    )


def _read(db_path: str, cache_key: str) -> dict | None:
    try:
        with sqlite3.connect(db_path) as con:
            _ensure_table(con)
            row = con.execute(
                "SELECT payload_json, version_key, updated_at "
                "FROM compute_cache WHERE cache_key = ?",
                (cache_key,),
            ).fetchone()
        if not row:
            return None
        return {
            "payload": json.loads(row[0]),
            "version_key": row[1] or "",
            "updated_at": row[2],
        }
    except Exception as e:
        logger.debug("compute_cache read failed: %s", e)
        return None


def _write(db_path: str, cache_key: str, payload: Any, version_key: str) -> None:
    """Persist a finished payload. Raises on failure — the caller records
    it in ``_errors`` so polls surface the problem instead of silently
    restarting the compute forever (a failed write + a successful
    compute_fn used to look like "nothing cached, nothing running")."""
    # default=str: pandas payloads legitimately carry date / Timestamp
    # objects in object-dtype columns; degrade them to ISO strings.
    blob = json.dumps(payload, ensure_ascii=False, default=str)
    with sqlite3.connect(db_path) as con:
        _ensure_table(con)
        con.execute(
            "INSERT INTO compute_cache "
            "(cache_key, payload_json, version_key, updated_at) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(cache_key) DO UPDATE SET "
            "payload_json = excluded.payload_json, "
            "version_key = excluded.version_key, "
            "updated_at = excluded.updated_at",
            (cache_key, blob, version_key, time.time()),
        )
        con.commit()


def _start_background(
    db_path: str, cache_key: str, version_key: str,
    compute_fn: Callable[[], Any],
) -> bool:
    """Start the compute on a daemon thread unless one is already
    running for this key. Returns True if a new run started."""
    now = time.time()
    with _lock:
        started = _in_progress.get(cache_key)
        if started is not None and now - started < _IN_PROGRESS_TTL:
            return False
        _in_progress[cache_key] = now
        _errors.pop(cache_key, None)

    def _run() -> None:
        try:
            payload = compute_fn()
            _write(db_path, cache_key, payload, version_key)
        except Exception as e:
            logger.exception("compute_cache job %s failed", cache_key)
            with _lock:
                _errors[cache_key] = str(e)
        finally:
            with _lock:
                _in_progress.pop(cache_key, None)

    threading.Thread(
        target=_run, name=f"compute-{cache_key[:32]}", daemon=True,
    ).start()
    return True


def get_or_compute(
    db_path: str, cache_key: str, version_key: str,
    compute_fn: Callable[[], Any],
    *, refresh: bool = False, cached_only: bool = False,
) -> dict[str, Any]:
    """Instant-response cache protocol.

    Returns one of:
    - {state: 'ready', payload, stale, computing, updated_at}
      ``stale=True`` when the source version changed since the cached
      run (UI shows 数据已更新提示); ``computing=True`` when a refresh
      is running in the background while the old payload is shown.
    - {state: 'computing'} — nothing cached yet, work started (or
      already running). Poll again.
    - {state: 'empty'} — nothing cached and ``cached_only`` was set
      (懒加载: do NOT auto-start heavy work on page mount).
    - {state: 'error', error} — last background run failed and there
      is no cached payload to fall back to.
    """
    cached = _read(db_path, cache_key)
    with _lock:
        computing = cache_key in _in_progress
        last_error = _errors.get(cache_key)

    if cached is not None:
        stale = bool(version_key) and cached["version_key"] != version_key
        # Stale alone does NOT auto-recompute — the UI shows a 数据已更新
        # banner and the user decides when to 重新分析 (spec UI设计·机制4).
        if refresh:
            started = _start_background(db_path, cache_key, version_key, compute_fn)
            computing = computing or started
        return {
            "state": "ready",
            "payload": cached["payload"],
            "stale": stale,
            "computing": computing,
            "updated_at": cached["updated_at"],
        }

    if computing:
        return {"state": "computing"}
    if cached_only:
        if last_error:
            return {"state": "error", "error": last_error}
        return {"state": "empty"}
    if last_error and not refresh:
        return {"state": "error", "error": last_error}
    _start_background(db_path, cache_key, version_key, compute_fn)
    return {"state": "computing"}


def reset_for_tests() -> None:
    with _lock:
        _in_progress.clear()
        _errors.clear()
