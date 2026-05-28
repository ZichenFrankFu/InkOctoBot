"""Commit-pipeline task tracking API (spec § 模块 1).

Read endpoints return DB-backed task state (survives restart).
Write endpoints (retry / skip) provide manual recovery for tasks
that landed in ``failed_needs_manual`` or ``failed_will_retry``.
"""
from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, HTTPException, Query

from ..services.commit_pipeline import (
    pipeline as pipeline_mod,
    sub_tasks as sub_tasks_mod,
    task_registry,
)
from ..services.project_paths import get_db_path

logger = logging.getLogger("inkoctobot.routers.commit_pipeline_api")


router = APIRouter(prefix="/api/commit-pipeline", tags=["commit-pipeline"])


@router.get("/status/{chapter_id}")
def status_for_chapter(chapter_id: str) -> dict:
    rows = task_registry.list_for_chapter(get_db_path(), chapter_id)
    return {
        "chapter_id": chapter_id,
        "tasks": [r.to_dict() for r in rows],
    }


@router.get("/tasks/{task_id}")
def get_task(task_id: str) -> dict:
    row = task_registry.get(get_db_path(), task_id)
    if row is None:
        raise HTTPException(404, f"no commit task with id {task_id!r}")
    return row.to_dict()


@router.get("/failed-tasks")
def list_failed(
    project_id: str = Query(...),
    include_will_retry: bool = Query(default=False),
) -> dict:
    rows = task_registry.list_failed_for_project(
        get_db_path(), project_id, include_will_retry=include_will_retry,
    )
    return {
        "project_id": project_id,
        "tasks": [r.to_dict() for r in rows],
    }


@router.post("/tasks/{task_id}/retry")
def retry_task(task_id: str) -> dict:
    """Manually re-run a failed task. Spawns a fresh sub-task attempt
    that respects the same retry schedule from the start."""
    db_path = get_db_path()
    row = task_registry.get(db_path, task_id)
    if row is None:
        raise HTTPException(404, f"no commit task with id {task_id!r}")
    sub_def = sub_tasks_mod.get_sub_task(row.task_type)
    if sub_def is None:
        raise HTTPException(
            400, f"unknown task_type {row.task_type!r}; cannot retry",
        )
    # Reset state + counters, then schedule a fresh run.
    task_registry.update(
        db_path, task_id,
        state="pending", retry_count=0,
        error_message="", error_stack="",
    )
    ctx = sub_tasks_mod.SubTaskContext(
        project_id=row.project_id,
        chapter_id=row.chapter_id,
        db_path=db_path,
    )

    async def _retry() -> None:
        await pipeline_mod._run_with_retries(row, sub_def, ctx)

    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_retry(), name=f"commit_pipeline:retry:{task_id}")
    except RuntimeError:
        import threading
        threading.Thread(
            target=lambda: asyncio.run(_retry()),
            name=f"commit-pipeline-retry-{task_id}",
            daemon=True,
        ).start()
    return {"ok": True, "task_id": task_id, "state": "pending"}


@router.post("/tasks/{task_id}/skip")
def skip_task(task_id: str) -> dict:
    """Mark a failed task as 'skipped'. The chapter advances without
    this task's output; subsequent commit triggers won't re-spawn it."""
    from datetime import datetime
    db_path = get_db_path()
    row = task_registry.get(db_path, task_id)
    if row is None:
        raise HTTPException(404, f"no commit task with id {task_id!r}")
    task_registry.update(
        db_path, task_id,
        state="skipped",
        completed_at=datetime.utcnow().isoformat(timespec="seconds"),
        user_acknowledged=True,
    )
    return {"ok": True, "task_id": task_id, "state": "skipped"}
