"""Persistent user-notifications service.

Two writes happen on every ``create_notification`` call:

1. INSERT into ``user_notifications`` (persistent history, queryable
   later via the notification-center page even after restart).
2. ``EventBus.push_suggestion`` with an :class:`AgentSuggestion` so
   the existing frontend WebSocket subscriber lights up the bell
   icon in real time without polling.

The EventBus is in-memory and ephemeral; the DB row is the source of
truth. The bus is a UX nicety — if the listener missed it (page not
open, restart between create-time and read-time), the user still
sees the entry on their next /api/notifications fetch.
"""
from __future__ import annotations

import logging
import sqlite3
import uuid
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("inkoctobot.services.notifications")


# Severity → EventBus severity mapping. The notification-center model
# uses high/medium/low; the EventBus AgentSuggestion model uses
# info/warning/suggestion. Map the strongest signal so a "high" alert
# (e.g. summarizer keeps failing) still reads as a "warning" in the
# realtime stream.
_SEVERITY_TO_BUS: dict[str, str] = {
    "high":   "warning",
    "medium": "suggestion",
    "low":    "info",
}


@dataclass
class Notification:
    notification_id: str
    project_id: str
    chapter_id: str | None
    notification_type: str
    severity: str
    title: str
    description: str = ""
    action_label: str = ""
    action_url: str = ""
    related_task_id: str = ""
    related_entity_id: str = ""
    created_at: str = ""
    read_at: str | None = None
    acknowledged_at: str | None = None
    auto_dismiss_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "notification_id":   self.notification_id,
            "project_id":        self.project_id,
            "chapter_id":        self.chapter_id,
            "notification_type": self.notification_type,
            "severity":          self.severity,
            "title":             self.title,
            "description":       self.description,
            "action_label":      self.action_label,
            "action_url":        self.action_url,
            "related_task_id":   self.related_task_id,
            "related_entity_id": self.related_entity_id,
            "created_at":        self.created_at,
            "read_at":           self.read_at,
            "acknowledged_at":   self.acknowledged_at,
            "auto_dismiss_at":   self.auto_dismiss_at,
        }


def _new_id() -> str:
    return f"nt_{uuid.uuid4().hex[:12]}"


def create_notification(
    db_path: str,
    *,
    project_id: str,
    notification_type: str,
    severity: str,
    title: str,
    chapter_id: str | None = None,
    description: str = "",
    action_label: str = "",
    action_url: str = "",
    related_task_id: str = "",
    related_entity_id: str = "",
    push_to_event_bus: bool = True,
) -> Notification:
    """Persist a notification + (optionally) push to EventBus.

    Returns the inserted Notification (with assigned id + created_at).
    """
    if severity not in ("high", "medium", "low"):
        raise ValueError(f"severity must be high/medium/low, got {severity!r}")

    notif = Notification(
        notification_id=_new_id(),
        project_id=project_id,
        chapter_id=chapter_id,
        notification_type=notification_type,
        severity=severity,
        title=title,
        description=description,
        action_label=action_label,
        action_url=action_url,
        related_task_id=related_task_id,
        related_entity_id=related_entity_id,
    )

    with sqlite3.connect(db_path) as con:
        con.execute(
            "INSERT INTO user_notifications "
            "(notification_id, project_id, chapter_id, notification_type, "
            " severity, title, description, action_label, action_url, "
            " related_task_id, related_entity_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (notif.notification_id, project_id, chapter_id,
             notification_type, severity, title, description,
             action_label, action_url, related_task_id, related_entity_id),
        )
        con.commit()
        row = con.execute(
            "SELECT created_at FROM user_notifications WHERE notification_id = ?",
            (notif.notification_id,),
        ).fetchone()
        if row:
            notif.created_at = row[0]

    if push_to_event_bus:
        try:
            from framework.event_types import AgentSuggestion
            from framework.global_event_bus import get_event_bus
            bus = get_event_bus()
            bus.push_suggestion(AgentSuggestion(
                agent_name="post_commit_pipeline",
                title=title,
                content=description or title,
                severity=_SEVERITY_TO_BUS.get(severity, "info"),
                event_type=f"notification.{notification_type}",
                project_id=project_id,
                data={
                    "notification_id":  notif.notification_id,
                    "action_label":     action_label,
                    "action_url":       action_url,
                    "related_task_id":  related_task_id,
                    "severity":         severity,
                    "chapter_id":       chapter_id,
                },
            ))
        except Exception as e:
            # EventBus push is best-effort — if global bus isn't wired
            # (test environments, etc.) the DB row still exists and
            # the user will pick it up via /api/notifications.
            logger.debug("EventBus push skipped: %s", e)

    return notif


def list_notifications(
    db_path: str,
    *,
    project_id: str,
    unread_only: bool = False,
    severity: str | None = None,
    limit: int = 50,
) -> list[Notification]:
    """List notifications for a project, newest first."""
    where = ["project_id = ?"]
    params: list[Any] = [project_id]
    if unread_only:
        where.append("read_at IS NULL")
    if severity:
        where.append("severity = ?")
        params.append(severity)
    sql = (
        "SELECT notification_id, project_id, chapter_id, notification_type, "
        "       severity, title, description, action_label, action_url, "
        "       related_task_id, related_entity_id, created_at, read_at, "
        "       acknowledged_at, auto_dismiss_at "
        "FROM user_notifications "
        f"WHERE {' AND '.join(where)} "
        "ORDER BY created_at DESC LIMIT ?"
    )
    params.append(limit)
    out: list[Notification] = []
    with sqlite3.connect(db_path) as con:
        con.row_factory = sqlite3.Row
        for r in con.execute(sql, params):
            out.append(Notification(**dict(r)))
    return out


def mark_read(db_path: str, notification_id: str) -> bool:
    with sqlite3.connect(db_path) as con:
        cur = con.execute(
            "UPDATE user_notifications SET read_at = CURRENT_TIMESTAMP "
            "WHERE notification_id = ? AND read_at IS NULL",
            (notification_id,),
        )
        con.commit()
        return cur.rowcount > 0


def acknowledge(db_path: str, notification_id: str) -> bool:
    with sqlite3.connect(db_path) as con:
        cur = con.execute(
            "UPDATE user_notifications "
            "SET acknowledged_at = CURRENT_TIMESTAMP, "
            "    read_at = COALESCE(read_at, CURRENT_TIMESTAMP) "
            "WHERE notification_id = ? AND acknowledged_at IS NULL",
            (notification_id,),
        )
        con.commit()
        return cur.rowcount > 0
