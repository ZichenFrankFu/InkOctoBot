"""
Structured logging configuration.

Migrated from root log_setup.py — identical behaviour, new import path.
Call setup_logging() once at application entry point.
"""
from __future__ import annotations

import logging
import sys
from datetime import datetime
from pathlib import Path


def setup_logging(
    *,
    log_dir: str | Path = "outputs/logs",
    console_level: int = logging.INFO,
    file_level: int = logging.DEBUG,
    log_filename: str | None = None,
) -> None:
    """
    One-time root logger configuration.

    - File handler: DEBUG level (everything, for post-mortem debugging)
    - Console handler: INFO level (progress for user)
    - In UI mode, console can be set to WARNING (logs via API)
    """
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)

    if log_filename is None:
        log_filename = f"inkoctobot_{datetime.now():%Y%m%d_%H%M%S}.log"

    log_path = log_dir / log_filename

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    root.handlers.clear()

    # ---- File handler ----
    fmt_file = logging.Formatter(
        "%(asctime)s | %(name)-30s | %(levelname)-7s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    fh = logging.FileHandler(str(log_path), encoding="utf-8")
    fh.setLevel(file_level)
    fh.setFormatter(fmt_file)
    root.addHandler(fh)

    # ---- Console handler ----
    fmt_console = logging.Formatter(
        "%(asctime)s | %(levelname)-7s | %(message)s",
        datefmt="%H:%M:%S",
    )
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(console_level)
    ch.setFormatter(fmt_console)
    root.addHandler(ch)

    # ---- Quiet noisy third-party loggers ----
    for noisy in [
        "urllib3", "selenium", "undetected_chromedriver",
        "httpcore", "httpx", "uvicorn.access",
    ]:
        logging.getLogger(noisy).setLevel(logging.WARNING)

    logging.getLogger("inkoctobot").info("Logging initialised: %s", log_path)
