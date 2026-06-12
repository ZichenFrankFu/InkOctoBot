"""/api/perf — 性能预期 (spec 5.7.2).

机制1: 耗时操作执行前给出预计耗时 — GET /estimate 按任务类型 +
条目数返回 ETA（机制3: 同硬件历史中位速率优先，无历史回退内置
默认值）。机制2 的剩余时间换算由 GET /remaining 提供给进度条用。
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

router = APIRouter(prefix="/api/perf", tags=["perf"])


def _db() -> str:
    from ..services.project_paths import get_db_path
    return get_db_path()


@router.get("/estimate")
def estimate(
    task_type: str = Query(...),
    item_count: int = Query(default=1, ge=1),
):
    from ..services.duration_estimator import estimate as _estimate
    return _estimate(_db(), task_type, item_count)


@router.get("/remaining")
def remaining(
    estimated_seconds: float = Query(..., ge=0),
    done: int = Query(..., ge=0),
    total: int = Query(..., ge=1),
):
    from ..services.duration_estimator import remaining as _remaining
    return _remaining(estimated_seconds, done, total)


@router.get("/hardware")
def hardware():
    """检测到的硬件签名（估算分桶依据）。"""
    from ..services.duration_estimator import hardware_key
    return {"hardware_key": hardware_key()}
