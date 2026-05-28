"""SnapshotReminder — surface overdue snapshot transitions.

LOADER_SPEC Loader 5, Batch 5. Two reminder types:

- ``snapshot_not_started``                  — chapter past
                                              ``expected_chapter_range_start + 5``
                                              and the snapshot has no
                                              bound chapters yet.
- ``snapshot_transition_not_completed``     — chapter past
                                              ``expected_chapter_range_end``,
                                              snapshot has bound
                                              chapters, but no
                                              ``transition_complete_chapter``.

Each (snapshot_id, type, chapter_num) emits at most once: the
``character_snapshot_reminders`` table has a UNIQUE constraint on
that triple, and ``INSERT OR IGNORE`` keeps re-checks idempotent.
"""
from __future__ import annotations

import logging
import sqlite3
import time
import uuid
from typing import Any

from . import snapshot_store

logger = logging.getLogger("inkoctobot.services.snapshot_reminder")


_NOT_STARTED_LAG = 5  # chapters past expected_start before nagging


def _rid() -> str:
    return f"rem_{int(time.time() * 1000)}_{uuid.uuid4().hex[:6]}"


def _try_record(
    db_path: str, snapshot_id: str, reminder_type: str, chapter_num: int,
) -> bool:
    """Atomic check + insert. Returns True if this is a brand-new emit."""
    from .project_store import open_db
    with open_db(db_path) as con:
        cur = con.execute(
            "INSERT OR IGNORE INTO character_snapshot_reminders "
            "(reminder_id, snapshot_id, reminder_type, triggered_at_chapter) "
            "VALUES (?, ?, ?, ?)",
            (_rid(), snapshot_id, reminder_type, chapter_num),
        )
        con.commit()
        return cur.rowcount > 0


def check_overdue_reminders(
    db_path: str, project_id: str, current_chapter_num: int,
) -> list[dict[str, Any]]:
    """Return new reminders triggered at ``current_chapter_num``.

    Side effect: persists each emit to ``character_snapshot_reminders``
    so the same reminder won't fire again on later prompt builds for
    the same chapter.
    """
    out: list[dict[str, Any]] = []
    for snap in snapshot_store.list_project_snapshots(db_path, project_id):
        # Snapshot is done — nothing to nag about.
        if snap.get("transition_complete_chapter") is not None:
            continue
        sid = snap["snapshot_id"]
        bound = list(snap.get("bound_chapters") or [])
        start = snap.get("expected_chapter_range_start")
        end = snap.get("expected_chapter_range_end")

        # Case 1: no bound chapters AND we're past expected_start + lag.
        if not bound:
            if start is None:
                continue
            if current_chapter_num <= int(start) + _NOT_STARTED_LAG:
                continue
            if not _try_record(
                db_path, sid, "snapshot_not_started", current_chapter_num,
            ):
                continue
            out.append({
                "snapshot_id":     sid,
                "character_id":    snap.get("character_id"),
                "type":            "snapshot_not_started",
                "snapshot_order":  snap.get("snapshot_order"),
                "expected_at":     int(start),
                "current":         current_chapter_num,
                "trigger":         snap.get("trigger_description") or "",
            })
            continue

        # Case 2: bound chapters exist but no transition_complete_chapter,
        # and we're past expected_end.
        if end is None or current_chapter_num <= int(end):
            continue
        if not _try_record(
            db_path, sid, "snapshot_transition_not_completed",
            current_chapter_num,
        ):
            continue
        out.append({
            "snapshot_id":         sid,
            "character_id":        snap.get("character_id"),
            "type":                "snapshot_transition_not_completed",
            "snapshot_order":      snap.get("snapshot_order"),
            "bound_so_far":        bound,
            "expected_complete_by": int(end),
            "current":             current_chapter_num,
        })
    return out


def list_outstanding_reminders(
    db_path: str, project_id: str,
) -> list[dict[str, Any]]:
    """Reminders not yet acknowledged. Joined with the snapshot row so
    the UI can surface character + snapshot context."""
    from .project_store import open_db
    out: list[dict[str, Any]] = []
    with open_db(db_path) as con:
        rows = con.execute(
            "SELECT r.reminder_id, r.snapshot_id, r.reminder_type, "
            "r.triggered_at_chapter, r.user_acknowledged, r.user_action, "
            "r.created_at, s.character_id, s.snapshot_order, s.project_id "
            "FROM character_snapshot_reminders r "
            "JOIN character_snapshots s ON s.snapshot_id = r.snapshot_id "
            "WHERE s.project_id = ? AND r.user_acknowledged = 0 "
            "ORDER BY r.created_at DESC",
            (project_id,),
        ).fetchall()
        for r in rows:
            out.append({
                "reminder_id":           r["reminder_id"],
                "snapshot_id":           r["snapshot_id"],
                "character_id":          r["character_id"],
                "snapshot_order":        r["snapshot_order"],
                "type":                  r["reminder_type"],
                "triggered_at_chapter":  r["triggered_at_chapter"],
                "created_at":            r["created_at"],
            })
    return out


def acknowledge_reminder(
    db_path: str, reminder_id: str, action: str,
) -> dict[str, Any] | None:
    """``action`` ∈ {'confirmed','postponed','cancelled'}. Returns the
    updated row or None on unknown id."""
    if action not in ("confirmed", "postponed", "cancelled"):
        raise ValueError(
            f"action must be one of 'confirmed','postponed','cancelled'; got {action!r}"
        )
    from .project_store import open_db
    with open_db(db_path) as con:
        cur = con.execute(
            "UPDATE character_snapshot_reminders "
            "SET user_acknowledged = 1, user_action = ? "
            "WHERE reminder_id = ?",
            (action, reminder_id),
        )
        if cur.rowcount == 0:
            return None
        con.commit()
        row = con.execute(
            "SELECT reminder_id, snapshot_id, reminder_type, "
            "triggered_at_chapter, user_acknowledged, user_action "
            "FROM character_snapshot_reminders WHERE reminder_id = ?",
            (reminder_id,),
        ).fetchone()
    return dict(row) if row else None
