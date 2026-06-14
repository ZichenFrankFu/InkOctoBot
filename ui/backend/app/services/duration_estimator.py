"""性能预期 (spec 5.7.2) — 耗时操作的预计耗时估算.

机制1: 耗时操作（特征提取、embedding、批量生成）执行前给出预计耗时。
机制3: 预计耗时依据硬件检测结果与历史任务耗时估算 — every finished
run records (task_type, item_count, seconds, hardware_key); estimates
use the median per-item rate of recent same-hardware runs, falling
back to built-in defaults when no history exists yet. 机制2 的进度条
由各任务自己的 progress 通道负责，本模块提供 ETA 与剩余时间换算。
"""
from __future__ import annotations

import logging
import sqlite3
import statistics
import uuid
from typing import Any

logger = logging.getLogger("inkoctobot.services.duration_estimator")


# Built-in per-item seconds when no history exists (cold start).
# Keys are free-form task types; callers register theirs here or pass
# their own default via estimate(default_per_item=...).
DEFAULT_PER_ITEM_SECONDS: dict[str, float] = {
    "embedding_batch_reindex": 0.15,   # per text row (CPU-ish baseline)
    "reference_index_l1":      0.2,    # per outline/character/setting entry
    "reference_index_l2":      0.25,   # per chapter summary
    "reference_index_l3":      0.5,    # per chapter (chunked)
    "reference_feature_extract": 45.0, # per segment (one LLM call)
    "market_extract_work":     30.0,   # per work
    "chapter_generation":      90.0,   # per chapter (single agent)
}

_MAX_SAMPLES = 12


def _ensure_table(con: sqlite3.Connection) -> None:
    con.execute(
        """CREATE TABLE IF NOT EXISTS task_duration_history (
               run_id TEXT PRIMARY KEY,
               task_type TEXT NOT NULL,
               item_count INTEGER NOT NULL DEFAULT 1,
               duration_seconds REAL NOT NULL,
               hardware_key TEXT NOT NULL DEFAULT 'cpu',
               created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
           )"""
    )
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_duration_history "
        "ON task_duration_history(task_type, hardware_key, created_at)"
    )


def hardware_key() -> str:
    """Coarse hardware signature so history from a GPU box doesn't
    poison estimates after moving to CPU (机制3)."""
    try:
        from ui.backend.app.services.embedding.hardware_detector import (
            detect_hardware,
        )
        caps = detect_hardware()
        if getattr(caps, "has_cuda", False):
            return f"cuda:{getattr(caps, 'cuda_device_name', '') or 'gpu'}"
        if getattr(caps, "has_mps", False):
            return "mps"
    except Exception:
        pass
    return "cpu"


def record_duration(
    db_path: str, task_type: str, item_count: int, duration_seconds: float,
    *, hw_key: str | None = None,
) -> None:
    """Append one finished run. Never raises — recording must not break
    the task that just succeeded."""
    try:
        if duration_seconds <= 0 or item_count <= 0:
            return
        with sqlite3.connect(db_path) as con:
            _ensure_table(con)
            con.execute(
                "INSERT INTO task_duration_history "
                "(run_id, task_type, item_count, duration_seconds, "
                " hardware_key) VALUES (?, ?, ?, ?, ?)",
                (f"tdh_{uuid.uuid4().hex[:12]}", task_type,
                 int(item_count), float(duration_seconds),
                 hw_key or hardware_key()),
            )
            con.commit()
    except Exception as e:
        logger.debug("duration record skipped: %s", e)


def estimate(
    db_path: str, task_type: str, item_count: int = 1,
    *, default_per_item: float | None = None, hw_key: str | None = None,
) -> dict[str, Any]:
    """Pre-run ETA (机制1). Returns
    ``{estimated_seconds, per_item_seconds, basis, samples,
    hardware_key, display}``."""
    item_count = max(1, int(item_count))
    hw = hw_key or hardware_key()
    rates: list[float] = []
    try:
        with sqlite3.connect(db_path) as con:
            _ensure_table(con)
            rows = con.execute(
                "SELECT item_count, duration_seconds "
                "FROM task_duration_history "
                "WHERE task_type = ? AND hardware_key = ? "
                "ORDER BY created_at DESC LIMIT ?",
                (task_type, hw, _MAX_SAMPLES),
            ).fetchall()
        rates = [
            d / max(1, n) for n, d in rows
            if d and d > 0
        ]
    except Exception as e:
        logger.debug("duration history read skipped: %s", e)

    if rates:
        per_item = statistics.median(rates)
        basis = "history"
    else:
        per_item = (
            default_per_item
            if default_per_item is not None
            else DEFAULT_PER_ITEM_SECONDS.get(task_type, 1.0)
        )
        basis = "default"
    seconds = per_item * item_count
    return {
        "task_type": task_type,
        "item_count": item_count,
        "per_item_seconds": round(per_item, 3),
        "estimated_seconds": round(seconds, 1),
        "basis": basis,
        "samples": len(rates),
        "hardware_key": hw,
        "display": format_duration(seconds),
    }


def remaining(
    estimated_seconds: float, done: int, total: int,
) -> dict[str, Any]:
    """机制2 helper: remaining-time from progress."""
    total = max(1, total)
    done = min(max(0, done), total)
    left = estimated_seconds * (1 - done / total)
    return {
        "remaining_seconds": round(left, 1),
        "display": format_duration(left),
        "progress": round(done / total, 4),
    }


def format_duration(seconds: float) -> str:
    s = max(0, int(round(seconds)))
    if s < 60:
        return f"约 {s} 秒"
    if s < 3600:
        m, sec = divmod(s, 60)
        return f"约 {m} 分{f' {sec} 秒' if sec else ''}"
    h, rem = divmod(s, 3600)
    m = rem // 60
    return f"约 {h} 小时{f' {m} 分' if m else ''}"
