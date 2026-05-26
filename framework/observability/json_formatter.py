"""JSON line formatter for structured logging.

Enable by setting ``INKOCTO_LOG_JSON=1`` or by passing
``json_console=True`` to ``framework.log_setup.setup_logging``. When
on, the console emits one JSON object per line (parseable by jq,
Loki, etc.); the human-readable file format is unaffected.
"""
from __future__ import annotations

import json
import logging
import time


class JSONLineFormatter(logging.Formatter):
    """Format each LogRecord as a single JSON line.

    Fixed fields: ``ts`` (epoch seconds), ``level``, ``logger``,
    ``msg``. trace_id / session_id are added when the
    ``framework.observability.trace_context`` filter is installed.
    Exceptions are appended as ``exc``.
    """

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": round(record.created, 3),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        tid = getattr(record, "trace_id", "") or ""
        sid = getattr(record, "session_id", "") or ""
        if tid:
            payload["trace_id"] = tid
        if sid:
            payload["session_id"] = sid
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        # ``extra={"k": v}`` propagates as record attributes — surface
        # any non-builtin extras automatically.
        for k, v in record.__dict__.items():
            if k in payload or k.startswith("_") or k in _STD_ATTRS:
                continue
            try:
                json.dumps(v)  # keep only json-safe values
                payload[k] = v
            except Exception:
                continue
        return json.dumps(payload, ensure_ascii=False)


# LogRecord's built-in attributes that shouldn't be re-emitted as "extras".
_STD_ATTRS = frozenset({
    "args", "asctime", "created", "exc_info", "exc_text", "filename",
    "funcName", "levelname", "levelno", "lineno", "message", "module",
    "msecs", "msg", "name", "pathname", "process", "processName",
    "relativeCreated", "stack_info", "thread", "threadName",
    "trace_id", "session_id",  # surfaced as named fields above
    "taskName",
})


def fmt_iso8601(ts: float | None = None) -> str:
    """Return an ISO-8601 timestamp string (UTC)."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts or time.time()))
