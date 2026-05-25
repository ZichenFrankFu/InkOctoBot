"""In-memory ring buffer of recent log records.

Backs the ``/api/debug/recent-logs`` endpoint so developers can answer
"what just happened?" without tailing the log file. Size-capped so it
never consumes unbounded memory.
"""
from __future__ import annotations

import logging
from collections import deque
from threading import Lock
from typing import Any


class LogRingBuffer(logging.Handler):
    """A logging.Handler that keeps the last N records in memory.

    Each retained record is the formatted dict (level/logger/message/
    timestamp + trace context if present), not the raw LogRecord, so
    the buffer is JSON-serializable and survives outliving the record.
    """

    def __init__(self, capacity: int = 500):
        super().__init__()
        self._capacity = capacity
        self._records: deque[dict[str, Any]] = deque(maxlen=capacity)
        self._lock = Lock()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            entry = {
                "ts": record.created,
                "level": record.levelname,
                "logger": record.name,
                "message": record.getMessage(),
            }
            tid = getattr(record, "trace_id", "") or ""
            sid = getattr(record, "session_id", "") or ""
            if tid:
                entry["trace_id"] = tid
            if sid:
                entry["session_id"] = sid
            if record.exc_info:
                entry["exc"] = self.format(record).split("\n", 1)[-1]
            with self._lock:
                self._records.append(entry)
        except Exception:
            self.handleError(record)

    def recent(
        self,
        limit: int = 50,
        level: str | None = None,
        logger_prefix: str | None = None,
        trace_id: str | None = None,
        session_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return the most recent matching records (newest last)."""
        with self._lock:
            items = list(self._records)
        if level:
            items = [r for r in items if r["level"] == level.upper()]
        if logger_prefix:
            items = [r for r in items if r["logger"].startswith(logger_prefix)]
        if trace_id:
            items = [r for r in items if r.get("trace_id") == trace_id]
        if session_id:
            items = [r for r in items if r.get("session_id") == session_id]
        return items[-limit:]

    def clear(self) -> None:
        with self._lock:
            self._records.clear()


_buffer: LogRingBuffer | None = None


def get_buffer() -> LogRingBuffer:
    """Return the singleton ring buffer (creates it on first call)."""
    global _buffer
    if _buffer is None:
        _buffer = LogRingBuffer()
    return _buffer
