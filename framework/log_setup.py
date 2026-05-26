"""
Structured logging configuration.

Migrated from root log_setup.py. Call setup_logging() once at
application entry point.

Adds (over the original):
- INKOCTO_LOG_JSON=1 env var or json_console=True kwarg switches the
  console formatter to JSON-line for machine parsing
- Trace filter installs on every handler so trace_id / session_id from
  framework.observability.trace_context are auto-injected
- An in-memory log ring buffer (framework.observability.log_buffer) is
  attached so /api/debug/recent-logs can pull them without parsing files
"""
from __future__ import annotations

import logging
import os
import sys
from datetime import datetime
from pathlib import Path


def setup_logging(
    *,
    log_dir: str | Path = "outputs/logs",
    console_level: int = logging.INFO,
    file_level: int = logging.DEBUG,
    log_filename: str | None = None,
    json_console: bool | None = None,
    enable_log_buffer: bool = True,
) -> None:
    """One-time root logger configuration.

    Parameters
    ----------
    log_dir : where to write log files
    console_level / file_level : minimum level for each handler
    log_filename : custom file name (defaults to inkoctobot_<timestamp>.log)
    json_console : True to emit JSON-line on console; default reads
                   INKOCTO_LOG_JSON env var (any truthy value)
    enable_log_buffer : attach the in-memory ring buffer (used by
                        /api/debug/recent-logs)
    """
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)

    if log_filename is None:
        log_filename = f"inkoctobot_{datetime.now():%Y%m%d_%H%M%S}.log"

    log_path = log_dir / log_filename

    if json_console is None:
        json_console = bool(os.environ.get("INKOCTO_LOG_JSON", "").strip())

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    root.handlers.clear()

    # ---- File handler (always human-readable) ----
    # ``defaults`` (Python 3.10+) makes trace_id safe to format even
    # before the trace filter has populated it.
    fmt_file = logging.Formatter(
        "%(asctime)s | %(name)-30s | %(levelname)-7s | "
        "trace=%(trace_id)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        defaults={"trace_id": ""},
    )
    fh = logging.FileHandler(str(log_path), encoding="utf-8")
    fh.setLevel(file_level)
    fh.setFormatter(fmt_file)
    root.addHandler(fh)

    # ---- Console handler (human or JSON) ----
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(console_level)
    if json_console:
        from .observability.json_formatter import JSONLineFormatter
        ch.setFormatter(JSONLineFormatter())
    else:
        fmt_console = logging.Formatter(
            "%(asctime)s | %(levelname)-7s | %(message)s",
            datefmt="%H:%M:%S",
        )
        ch.setFormatter(fmt_console)
    root.addHandler(ch)

    # ---- In-memory ring buffer for /debug endpoint ----
    if enable_log_buffer:
        from .observability.log_buffer import get_buffer
        buf = get_buffer()
        buf.setLevel(logging.INFO)
        root.addHandler(buf)

    # ---- Trace context filter on every handler ----
    from .observability.trace_context import install_trace_filter
    install_trace_filter(root)

    # ---- Quiet noisy third-party loggers ----
    for noisy in [
        "urllib3", "selenium", "undetected_chromedriver",
        "httpcore", "httpx", "uvicorn.access",
    ]:
        logging.getLogger(noisy).setLevel(logging.WARNING)

    logging.getLogger("inkoctobot").info(
        "Logging initialised: %s (json_console=%s, buffer=%s)",
        log_path, json_console, enable_log_buffer,
    )
