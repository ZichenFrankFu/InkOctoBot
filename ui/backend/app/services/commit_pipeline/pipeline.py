"""Post-commit pipeline orchestrator.

When a chapter is committed (via ``/api/editor/save-version`` or the
legacy ``/api/json-storage/versions``), the handler calls
:func:`run_pipeline_async` which spawns one ``asyncio.create_task``
per enabled sub-task (Phase 1 ships 5 stub-enabled + 1 opt-in
disabled). Each task runs through :func:`_run_with_retries` so a
failure cascades into the retry schedule (1 / 5 / 30 min) and
ultimately into a user_notifications row + EventBus push.

Design constraints honored:

- Fire-and-forget from the HTTP handler — commit returns instantly,
  pipeline runs in background. The handler MUST NOT await; doing so
  would block the user.
- Per-task isolation — one task throwing doesn't poison the others;
  every task is wrapped in its own task with its own try/except.
- Database writes happen on the same project DB the handler opened;
  no cross-DB joins.
- Tests can call :func:`run_pipeline_sync` to bypass the asyncio
  scheduling and run inline.
"""
from __future__ import annotations

import asyncio
import logging
import time
import traceback
from datetime import datetime
from typing import Any

from . import retry as retry_mod
from . import task_registry
from .sub_tasks import (
    SubTaskContext, SubTaskDef, enabled_sub_tasks, manual_action_url_for,
)

logger = logging.getLogger("inkoctobot.commit_pipeline.pipeline")


def _now_iso() -> str:
    return datetime.utcnow().isoformat(timespec="seconds")


async def _run_with_retries(
    task_row: task_registry.CommitTaskRow,
    sub_task: SubTaskDef,
    ctx: SubTaskContext,
) -> None:
    """Run one sub-task, honoring the retry schedule.

    Catches every exception from the sub-task body so the caller
    doesn't need its own try/except. Final state transitions:

    - success at any attempt    → state='completed'
    - all attempts failed       → state='failed_needs_manual' + notification
    """
    max_attempts = retry_mod.max_retries() + 1   # initial + N retries

    for attempt in range(1, max_attempts + 1):
        task_registry.update(
            ctx.db_path, task_row.task_id,
            state="running",
            last_attempt_at=_now_iso(),
            started_at=_now_iso() if attempt == 1 else None,
        )
        try:
            result = await sub_task.run(ctx)
            task_registry.update(
                ctx.db_path, task_row.task_id,
                state="completed",
                completed_at=_now_iso(),
                output_summary=result if isinstance(result, dict) else {"result": result},
            )
            return
        except Exception as exc:
            err = str(exc)
            stack = traceback.format_exc()
            wait_s = retry_mod.wait_seconds_for_attempt(attempt)
            if wait_s is None:
                # Exhausted — escalate to user.
                task_registry.update(
                    ctx.db_path, task_row.task_id,
                    state="failed_needs_manual",
                    retry_count=attempt - 1,
                    error_message=err,
                    error_stack=stack,
                    completed_at=_now_iso(),
                )
                await _notify_failed(task_row, sub_task, ctx, err)
                return
            # Retry pending — record the state + sleep + loop.
            task_registry.update(
                ctx.db_path, task_row.task_id,
                state="failed_will_retry",
                retry_count=attempt,
                error_message=err,
                error_stack=stack,
            )
            logger.warning(
                "commit_task %s (%s) attempt %d failed: %s — retrying in %ds",
                task_row.task_id, sub_task.task_type, attempt, err, wait_s,
            )
            if wait_s > 0:
                await asyncio.sleep(wait_s)


async def _notify_failed(
    task_row: task_registry.CommitTaskRow,
    sub_task: SubTaskDef,
    ctx: SubTaskContext,
    error_msg: str,
) -> None:
    """Create a user_notifications row + push to EventBus."""
    try:
        from ui.backend.app.services.notifications import create_notification
        title = sub_task.notification_title_failed or f"{sub_task.task_type} 失败"
        desc = (
            (sub_task.notification_description_failed or "")
            + (f"\n\n错误：{error_msg[:200]}" if error_msg else "")
        )
        create_notification(
            ctx.db_path,
            project_id=ctx.project_id,
            chapter_id=ctx.chapter_id,
            notification_type=f"{sub_task.task_type}_failed",
            severity="high",
            title=title,
            description=desc,
            action_label="立即手动处理",
            action_url=manual_action_url_for(sub_task.task_type, ctx.chapter_id),
            related_task_id=task_row.task_id,
        )
        task_registry.update(ctx.db_path, task_row.task_id, user_notified=True)
    except Exception as e:
        # Notification failure shouldn't crash the pipeline — log and move on.
        logger.error(
            "failed to create user_notification for task %s: %s",
            task_row.task_id, e,
        )


def _make_task(ctx: SubTaskContext, sub_task: SubTaskDef) -> task_registry.CommitTaskRow:
    row = task_registry.CommitTaskRow(
        task_id=task_registry.new_task_id(),
        project_id=ctx.project_id,
        chapter_id=ctx.chapter_id,
        task_type=sub_task.task_type,
        state="pending",
        manual_action_url=manual_action_url_for(sub_task.task_type, ctx.chapter_id),
    )
    task_registry.insert(ctx.db_path, row)
    return row


async def run_pipeline_async(ctx: SubTaskContext) -> list[str]:
    """Schedule all enabled sub-tasks. Returns the list of task_ids.

    Each sub-task runs in its own ``asyncio.create_task`` so failures
    are isolated. The outer coroutine returns immediately after
    scheduling — caller should NOT await the tasks themselves.
    """
    task_ids: list[str] = []
    for sub_task in enabled_sub_tasks():
        row = _make_task(ctx, sub_task)
        task_ids.append(row.task_id)
        coro = _run_with_retries(row, sub_task, ctx)
        # Detach with a done-callback so unhandled exceptions get
        # logged rather than silently swallowed by the event loop.
        t = asyncio.create_task(
            coro, name=f"commit_pipeline:{sub_task.task_type}:{row.task_id}",
        )
        t.add_done_callback(_log_task_exception)
    return task_ids


def _log_task_exception(task: asyncio.Task) -> None:
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.error("commit_pipeline task crashed: %s", exc, exc_info=exc)


async def run_pipeline_sync(ctx: SubTaskContext) -> list[str]:
    """Test entry — schedule + await every task inline before returning."""
    task_ids: list[str] = []
    coros = []
    for sub_task in enabled_sub_tasks():
        row = _make_task(ctx, sub_task)
        task_ids.append(row.task_id)
        coros.append(_run_with_retries(row, sub_task, ctx))
    await asyncio.gather(*coros, return_exceptions=True)
    return task_ids


# ─────────── handler-side helper ───────────


def fire_and_forget_from_handler(
    project_id: str,
    chapter_id: str,
    db_path: str,
    chapter_text: str = "",
    chapter_num: int = 0,
) -> None:
    """Call from a FastAPI handler (async or sync).

    Wraps :func:`run_pipeline_async` so the handler doesn't have to
    care about the event loop. Any failure to schedule (e.g. no
    running loop in a sync handler) is logged and swallowed —
    commit must always succeed even if the pipeline can't start.

    Before kicking off the async sub-tasks, captures a
    chapter_snapshots row synchronously so the historical-view API
    can return a meaningful row even if the pipeline never completes.
    """
    # Phase 4: snapshot first (cheap, sync, no LLM). Swallow any failure.
    try:
        from .snapshot_writer import capture_snapshot
        capture_snapshot(db_path, project_id, chapter_id, chapter_num)
    except Exception as e:
        logger.error("chapter snapshot capture failed: %s", e)

    ctx = SubTaskContext(
        project_id=project_id, chapter_id=chapter_id, db_path=db_path,
        chapter_text=chapter_text, chapter_num=chapter_num,
    )
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # No loop — run in a daemon thread with its own loop. This
        # path handles sync handlers (the legacy json_storage_api
        # /versions endpoint) that don't share a loop with FastAPI.
        import threading

        def _runner() -> None:
            try:
                asyncio.run(run_pipeline_async(ctx))
            except Exception as e:
                logger.error("threaded pipeline launch failed: %s", e)

        threading.Thread(
            target=_runner,
            name=f"commit-pipeline-{chapter_id}",
            daemon=True,
        ).start()
        return

    # Async handler — schedule on the running loop.
    try:
        t = loop.create_task(run_pipeline_async(ctx))
        t.add_done_callback(_log_task_exception)
    except Exception as e:
        logger.error("failed to schedule commit pipeline: %s", e)
