"""Central compute-cache manager — the ONE module the app uses to store /
serve "saved, not-re-analyzed" feature-extraction data (market trend
analysis, opening-chapter NLP, …). All cache access goes through
``get_or_compute``; management (TTL auto-eviction, manual clear, stats)
goes through the helpers at the bottom.

Why it exists — the urgent fix for thread-pool exhaustion (恶性 bug: 多次
切换页面后全站无限加载): heavy sync endpoints used to run INSIDE the request
thread, draining the anyio worker pool. This module flips them to:

- responses are ALWAYS instant: cached payload (with ``stale`` flag when
  the source DB changed) or ``{state: 'computing'}``
- the compute runs on ONE dedicated background thread per cache key
  (single-flight — N page visits can't stack N runs)
- results persist in the project DB so the last finished analysis survives
  restarts (懒加载 + 保留上次结果 + 数据更新提醒)

Lifecycle management (this module's "cache manager" role):
- every cached row carries ``updated_at``; entries unused for longer than
  ``DEFAULT_TTL_SECONDS`` are auto-evicted on read AND by a background
  sweeper (``start_sweeper``), so the cache table can't grow without bound
- ``evict_expired`` / ``clear`` / ``stats`` expose manual management.
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

# Time-based auto-eviction: a cached entry not refreshed within this many
# seconds is considered stale-and-disposable and removed (read-time + a
# background sweeper). The source-DB ``version_key`` already invalidates
# content on data change; the TTL just bounds table growth for keys (old
# platforms / lookbacks) that are never revisited.
DEFAULT_TTL_SECONDS = 14 * 24 * 3600     # 14 天

_sweeper_started: set[str] = set()


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
    max_age: float = DEFAULT_TTL_SECONDS,
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

    ``max_age`` — drop (and recompute) a cached entry older than this many
    seconds (TTL auto-eviction).
    """
    cached = _read(db_path, cache_key)
    if (cached is not None and max_age and max_age > 0
            and time.time() - cached["updated_at"] > max_age):
        _delete(db_path, cache_key)     # TTL expired → treat as uncached
        cached = None
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


def _delete(db_path: str, cache_key: str) -> None:
    try:
        with sqlite3.connect(db_path) as con:
            _ensure_table(con)
            con.execute("DELETE FROM compute_cache WHERE cache_key = ?", (cache_key,))
            con.commit()
    except Exception as e:
        logger.debug("compute_cache delete failed: %s", e)


# ───────────────────────── cache management ─────────────────────────


def evict_expired(db_path: str, max_age: float = DEFAULT_TTL_SECONDS) -> int:
    """Delete cached entries not refreshed within ``max_age`` seconds.
    Returns the number removed."""
    cutoff = time.time() - max_age
    try:
        with sqlite3.connect(db_path) as con:
            _ensure_table(con)
            cur = con.execute(
                "DELETE FROM compute_cache WHERE updated_at < ?", (cutoff,))
            con.commit()
            return cur.rowcount or 0
    except Exception as e:
        logger.debug("compute_cache evict_expired failed: %s", e)
        return 0


def clear(db_path: str, prefix: str | None = None) -> int:
    """Clear the whole cache, or only keys starting with ``prefix``
    (e.g. ``"analysis_run"`` / ``"opening_nlp"``). Returns rows removed."""
    try:
        with sqlite3.connect(db_path) as con:
            _ensure_table(con)
            if prefix:
                cur = con.execute(
                    "DELETE FROM compute_cache WHERE cache_key LIKE ?",
                    (prefix + "%",))
            else:
                cur = con.execute("DELETE FROM compute_cache")
            con.commit()
            return cur.rowcount or 0
    except Exception as e:
        logger.debug("compute_cache clear failed: %s", e)
        return 0


def stats(db_path: str) -> dict[str, Any]:
    """Cache inventory: entry count, total payload bytes, oldest/newest age,
    plus the in-flight compute count."""
    out: dict[str, Any] = {"entries": 0, "total_bytes": 0,
                           "oldest_age_s": None, "newest_age_s": None,
                           "in_progress": 0}
    try:
        with sqlite3.connect(db_path) as con:
            _ensure_table(con)
            row = con.execute(
                "SELECT COUNT(*), COALESCE(SUM(LENGTH(payload_json)),0), "
                "MIN(updated_at), MAX(updated_at) FROM compute_cache"
            ).fetchone()
        now = time.time()
        out["entries"] = int(row[0] or 0)
        out["total_bytes"] = int(row[1] or 0)
        if row[2]:
            out["oldest_age_s"] = round(now - row[2])
        if row[3]:
            out["newest_age_s"] = round(now - row[3])
    except Exception as e:
        logger.debug("compute_cache stats failed: %s", e)
    with _lock:
        out["in_progress"] = len(_in_progress)
    return out


def start_sweeper(db_path: str, interval_s: float = 3600.0,
                  max_age: float = DEFAULT_TTL_SECONDS) -> None:
    """Start ONE background daemon (per db_path) that periodically evicts
    expired cache entries so the table can't grow unbounded. Idempotent."""
    with _lock:
        if db_path in _sweeper_started:
            return
        _sweeper_started.add(db_path)

    def _loop() -> None:
        while True:
            time.sleep(interval_s)
            try:
                n = evict_expired(db_path, max_age)
                if n:
                    logger.info("compute_cache sweeper evicted %d expired", n)
            except Exception:
                logger.debug("compute_cache sweeper iteration failed")

    threading.Thread(target=_loop, name="compute-cache-sweeper",
                     daemon=True).start()


def reset_for_tests() -> None:
    with _lock:
        _in_progress.clear()
        _errors.clear()
        _sweeper_started.clear()
