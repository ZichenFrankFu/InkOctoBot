"""In-memory + DB-backed registry for commit_tasks.

The pipeline writes through here so both the live status query
(``GET /api/commit-pipeline/status/<chapter_id>``) and the
persistent ``commit_tasks`` row stay in sync. The in-memory dict is
short-lived (cleared on restart) but the DB row survives — status
endpoints prefer the DB read so a server restart doesn't lose the
last commit's task history.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
import uuid
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("inkoctobot.commit_pipeline.task_registry")


TaskState = str  # 'pending'|'running'|'completed'|'failed_will_retry'|...


@dataclass
class CommitTaskRow:
    task_id: str
    project_id: str
    chapter_id: str
    task_type: str
    state: TaskState = "pending"
    retry_count: int = 0
    last_error_message: str = ""
    last_error_stack: str = ""
    started_at: str | None = None
    last_attempt_at: str | None = None
    completed_at: str | None = None
    output_summary: dict[str, Any] = field(default_factory=dict)
    user_notified: bool = False
    user_acknowledged: bool = False
    manual_action_url: str = ""
    created_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id":            self.task_id,
            "project_id":         self.project_id,
            "chapter_id":         self.chapter_id,
            "task_type":          self.task_type,
            "state":              self.state,
            "retry_count":        self.retry_count,
            "last_error_message": self.last_error_message,
            "last_error_stack":   self.last_error_stack,
            "started_at":         self.started_at,
            "last_attempt_at":    self.last_attempt_at,
            "completed_at":       self.completed_at,
            "output_summary":     self.output_summary,
            "user_notified":      self.user_notified,
            "user_acknowledged":  self.user_acknowledged,
            "manual_action_url":  self.manual_action_url,
            "created_at":         self.created_at,
        }


def new_task_id() -> str:
    return f"ct_{uuid.uuid4().hex[:12]}"


# ─────────── in-memory layer ───────────
_live: dict[str, CommitTaskRow] = {}
_lock = threading.Lock()


def remember(task: CommitTaskRow) -> None:
    with _lock:
        _live[task.task_id] = task


def get_live(task_id: str) -> CommitTaskRow | None:
    with _lock:
        return _live.get(task_id)


def reset_for_tests() -> None:
    with _lock:
        _live.clear()


# ─────────── DB layer ───────────

def insert(db_path: str, task: CommitTaskRow) -> None:
    """INSERT a fresh task row. Also remembers it in the live dict."""
    with sqlite3.connect(db_path) as con:
        con.execute(
            "INSERT INTO commit_tasks (task_id, project_id, chapter_id, "
            "task_type, state, manual_action_url) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (task.task_id, task.project_id, task.chapter_id,
             task.task_type, task.state, task.manual_action_url),
        )
        con.commit()
        row = con.execute(
            "SELECT created_at FROM commit_tasks WHERE task_id = ?",
            (task.task_id,),
        ).fetchone()
        if row:
            task.created_at = row[0]
    remember(task)


def update(
    db_path: str, task_id: str, *,
    state: TaskState | None = None,
    retry_count: int | None = None,
    error_message: str | None = None,
    error_stack: str | None = None,
    started_at: str | None = None,
    last_attempt_at: str | None = None,
    completed_at: str | None = None,
    output_summary: dict[str, Any] | None = None,
    user_notified: bool | None = None,
    user_acknowledged: bool | None = None,
) -> None:
    """Patch only the columns whose kwargs are non-None.

    Mirrors the same fields into the live dict so a status query
    between writes sees a consistent picture.
    """
    fields: dict[str, Any] = {}
    if state is not None:               fields["state"] = state
    if retry_count is not None:         fields["retry_count"] = retry_count
    if error_message is not None:       fields["last_error_message"] = error_message
    if error_stack is not None:         fields["last_error_stack"] = error_stack
    if started_at is not None:          fields["started_at"] = started_at
    if last_attempt_at is not None:     fields["last_attempt_at"] = last_attempt_at
    if completed_at is not None:        fields["completed_at"] = completed_at
    if output_summary is not None:      fields["output_summary"] = json.dumps(
                                            output_summary, ensure_ascii=False)
    if user_notified is not None:       fields["user_notified"] = int(user_notified)
    if user_acknowledged is not None:   fields["user_acknowledged"] = int(user_acknowledged)
    if not fields:
        return
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    params = list(fields.values()) + [task_id]
    with sqlite3.connect(db_path) as con:
        con.execute(
            f"UPDATE commit_tasks SET {set_clause} WHERE task_id = ?",
            params,
        )
        con.commit()

    # Mirror into live dict for fast status reads.
    with _lock:
        live = _live.get(task_id)
        if live is None:
            return
        if state is not None:              live.state = state
        if retry_count is not None:        live.retry_count = retry_count
        if error_message is not None:      live.last_error_message = error_message
        if error_stack is not None:        live.last_error_stack = error_stack
        if started_at is not None:         live.started_at = started_at
        if last_attempt_at is not None:    live.last_attempt_at = last_attempt_at
        if completed_at is not None:       live.completed_at = completed_at
        if output_summary is not None:     live.output_summary = output_summary
        if user_notified is not None:      live.user_notified = user_notified
        if user_acknowledged is not None:  live.user_acknowledged = user_acknowledged


def _row_to_obj(r: sqlite3.Row) -> CommitTaskRow:
    return CommitTaskRow(
        task_id=r["task_id"],
        project_id=r["project_id"],
        chapter_id=r["chapter_id"],
        task_type=r["task_type"],
        state=r["state"],
        retry_count=r["retry_count"],
        last_error_message=r["last_error_message"] or "",
        last_error_stack=r["last_error_stack"] or "",
        started_at=r["started_at"],
        last_attempt_at=r["last_attempt_at"],
        completed_at=r["completed_at"],
        output_summary=json.loads(r["output_summary"]) if r["output_summary"] else {},
        user_notified=bool(r["user_notified"]),
        user_acknowledged=bool(r["user_acknowledged"]),
        manual_action_url=r["manual_action_url"] or "",
        created_at=r["created_at"] or "",
    )


def get(db_path: str, task_id: str) -> CommitTaskRow | None:
    with sqlite3.connect(db_path) as con:
        con.row_factory = sqlite3.Row
        r = con.execute(
            "SELECT * FROM commit_tasks WHERE task_id = ?",
            (task_id,),
        ).fetchone()
    return _row_to_obj(r) if r else None


def list_for_chapter(db_path: str, chapter_id: str) -> list[CommitTaskRow]:
    with sqlite3.connect(db_path) as con:
        con.row_factory = sqlite3.Row
        rows = con.execute(
            "SELECT * FROM commit_tasks WHERE chapter_id = ? "
            "ORDER BY created_at",
            (chapter_id,),
        ).fetchall()
    return [_row_to_obj(r) for r in rows]


def list_failed_for_project(
    db_path: str, project_id: str, *,
    include_will_retry: bool = False,
) -> list[CommitTaskRow]:
    states = ["failed_needs_manual"]
    if include_will_retry:
        states.append("failed_will_retry")
    placeholders = ",".join("?" for _ in states)
    with sqlite3.connect(db_path) as con:
        con.row_factory = sqlite3.Row
        rows = con.execute(
            f"SELECT * FROM commit_tasks "
            f"WHERE project_id = ? AND state IN ({placeholders}) "
            f"ORDER BY created_at DESC",
            (project_id, *states),
        ).fetchall()
    return [_row_to_obj(r) for r in rows]
