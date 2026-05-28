"""Notification center API (spec § 模块 12).

Persistent inbox alongside the existing in-memory EventBus stream.
Use ``GET /api/notifications`` to render the bell-icon dropdown;
use ``POST .../acknowledge`` when the user clicks the row.
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from ..services.notifications import (
    acknowledge, list_notifications, mark_read,
)
from ..services.project_paths import get_db_path

logger = logging.getLogger("inkoctobot.routers.notifications_api")


router = APIRouter(prefix="/api/notifications", tags=["notifications"])


@router.get("")
def list_notifications_endpoint(
    project_id: str = Query(...),
    unread_only: bool = Query(default=False),
    severity: Optional[str] = Query(default=None),
    limit: int = Query(default=50, le=200),
) -> dict:
    if severity is not None and severity not in ("high", "medium", "low"):
        raise HTTPException(400, "severity must be one of high/medium/low")
    items = list_notifications(
        get_db_path(),
        project_id=project_id,
        unread_only=unread_only,
        severity=severity,
        limit=limit,
    )
    return {"notifications": [n.to_dict() for n in items]}


@router.post("/{notification_id}/mark-read")
def mark_read_endpoint(notification_id: str) -> dict:
    ok = mark_read(get_db_path(), notification_id)
    if not ok:
        raise HTTPException(
            404,
            f"notification {notification_id!r} not found or already read",
        )
    return {"ok": True, "notification_id": notification_id}


@router.post("/{notification_id}/acknowledge")
def acknowledge_endpoint(notification_id: str) -> dict:
    ok = acknowledge(get_db_path(), notification_id)
    if not ok:
        raise HTTPException(
            404,
            f"notification {notification_id!r} not found or already acknowledged",
        )
    return {"ok": True, "notification_id": notification_id}
