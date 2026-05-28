"""User notifications — persistent DB-backed inbox with EventBus mirror.

Public entry point: ``create_notification(...)`` writes a row to
``user_notifications`` AND pushes an AgentSuggestion onto the existing
in-memory EventBus so the frontend WebSocket gets it immediately.
"""
from .service import (
    Notification, create_notification,
    list_notifications, mark_read, acknowledge,
)

__all__ = [
    "Notification",
    "create_notification",
    "list_notifications",
    "mark_read",
    "acknowledge",
]
