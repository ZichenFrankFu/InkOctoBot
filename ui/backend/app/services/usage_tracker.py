"""LLM usage tracking with debounced persistence.

Tracks token usage by provider / model / agent_role across calls and
persists to ``data/usage.json``. The original code in generation_api.py
re-wrote the full file on every LLM call (hot-path I/O during streaming
generation). This module debounces writes: changes are committed to a
module-level dict immediately, but the file is flushed at most once per
``_DEBOUNCE_SECONDS`` window (plus on shutdown via ``flush_now()``).
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger("inkoctobot.services.usage_tracker")

_DEBOUNCE_SECONDS = 2.0
_MAX_RECENT_ENTRIES = 100


def _usage_file() -> Path:
    from ui.backend.app.settings import settings as _s
    d = _s.data_dir if _s.data_dir else _s.repo_root / "data"
    return d / "usage.json"


def _empty_usage() -> dict[str, Any]:
    return {
        "total_input_tokens": 0,
        "total_output_tokens": 0,
        "total_calls": 0,
        "by_provider": {},
        "by_model": {},
        "by_role": {},
        "recent": [],
    }


def _load_usage() -> dict[str, Any]:
    p = _usage_file()
    if p.exists():
        try:
            data = json.loads(p.read_text("utf-8"))
            empty = _empty_usage()
            for k, v in empty.items():
                data.setdefault(k, v)
            return data
        except Exception:
            logger.warning("usage.json malformed; starting from empty")
    return _empty_usage()


_data: dict[str, Any] = _load_usage()
_lock = asyncio.Lock()
_dirty: bool = False
_last_flush_at: float = time.monotonic()
_flusher_task: asyncio.Task | None = None


def _flush_to_disk() -> None:
    """Atomic write of the current snapshot to ``usage.json``."""
    global _dirty, _last_flush_at
    try:
        p = _usage_file()
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(_data, ensure_ascii=False, indent=2), "utf-8")
        tmp.replace(p)
        _dirty = False
        _last_flush_at = time.monotonic()
    except Exception:
        logger.debug("Failed to persist usage data", exc_info=True)


async def _flusher_loop() -> None:
    """Background task: every _DEBOUNCE_SECONDS, flush if dirty."""
    while True:
        try:
            await asyncio.sleep(_DEBOUNCE_SECONDS)
            if _dirty:
                _flush_to_disk()
        except asyncio.CancelledError:
            if _dirty:
                _flush_to_disk()
            raise
        except Exception:
            logger.debug("flusher loop error", exc_info=True)


def _ensure_flusher_running() -> None:
    """Start the background flusher on first use (when a loop exists)."""
    global _flusher_task
    if _flusher_task is not None and not _flusher_task.done():
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return  # No running loop: we'll flush on next async record_usage call
    _flusher_task = loop.create_task(_flusher_loop())


def record_usage(
    agent_role: str,
    provider: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
) -> None:
    """Record one LLM call. Mutates the in-memory snapshot, marks dirty.

    Safe to call from sync contexts. Disk persistence is debounced via
    a background async task; if no event loop is running yet we just
    accumulate and flush later.
    """
    global _dirty
    _data["total_input_tokens"] += input_tokens
    _data["total_output_tokens"] += output_tokens
    _data["total_calls"] += 1

    bucket = _data["by_provider"].setdefault(
        provider, {"input_tokens": 0, "output_tokens": 0, "calls": 0})
    bucket["input_tokens"] += input_tokens
    bucket["output_tokens"] += output_tokens
    bucket["calls"] += 1

    mbucket = _data["by_model"].setdefault(
        model, {"input_tokens": 0, "output_tokens": 0, "calls": 0})
    mbucket["input_tokens"] += input_tokens
    mbucket["output_tokens"] += output_tokens
    mbucket["calls"] += 1

    rbucket = _data["by_role"].setdefault(
        agent_role, {"input_tokens": 0, "output_tokens": 0, "calls": 0})
    rbucket["input_tokens"] += input_tokens
    rbucket["output_tokens"] += output_tokens
    rbucket["calls"] += 1

    _data["recent"].append({
        "timestamp": time.time(),
        "agent_role": agent_role,
        "provider": provider,
        "model": model,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
    })
    if len(_data["recent"]) > _MAX_RECENT_ENTRIES:
        _data["recent"] = _data["recent"][-_MAX_RECENT_ENTRIES:]

    _dirty = True
    _ensure_flusher_running()


def snapshot() -> dict[str, Any]:
    """Return a deep-copyable snapshot of current usage."""
    return json.loads(json.dumps(_data))


def reset() -> None:
    """Reset all tracked usage to zero. Flushes immediately."""
    global _data
    _data = _empty_usage()
    _flush_to_disk()


def flush_now() -> None:
    """Force a sync flush. Call on app shutdown."""
    if _dirty:
        _flush_to_disk()
