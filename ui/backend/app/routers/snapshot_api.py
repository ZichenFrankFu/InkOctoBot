"""Character-snapshot API (LOADER_SPEC Loader 5, Batch 5).

11 endpoints covering CRUD, bind / unbind, mark-complete / unmark,
auto-detection, and reminders. The router lives at ``/api/snapshots``
(individual snapshots) and ``/api/snapshot-reminders`` (the reminder
queue).

Auto-detection is async and tolerant — failures degrade to no
candidates, never a 500.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Body, HTTPException, Query

from ..services import (
    project_paths,
    snapshot_auto_detector,
    snapshot_reminder,
    snapshot_store,
)

logger = logging.getLogger("inkoctobot.routers.snapshot_api")


router = APIRouter(prefix="/api/snapshots", tags=["snapshots"])
reminder_router = APIRouter(
    prefix="/api/snapshot-reminders", tags=["snapshot-reminders"],
)


def _db() -> str:
    return project_paths.get_db_path()


# ─────────── CRUD ───────────


@router.get("")
def list_snapshots(character_id: str = Query(...)):
    return {"items": snapshot_store.list_snapshots(_db(), character_id)}


@router.get("/{snapshot_id}")
def get_snapshot(snapshot_id: str):
    snap = snapshot_store.get_snapshot(_db(), snapshot_id)
    if snap is None:
        raise HTTPException(404, f"snapshot {snapshot_id} not found")
    return snap


@router.post("")
def create_snapshot(body: dict = Body(...)):
    try:
        return snapshot_store.upsert_snapshot(_db(), body)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.put("/{snapshot_id}")
def update_snapshot(snapshot_id: str, body: dict = Body(...)):
    body = {**body, "snapshot_id": snapshot_id}
    try:
        return snapshot_store.upsert_snapshot(_db(), body)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.delete("/{snapshot_id}")
def delete_snapshot(snapshot_id: str):
    snapshot_store.delete_snapshot(_db(), snapshot_id)
    return {"ok": True}


# ─────────── bind / mark-complete ───────────


@router.post("/{snapshot_id}/bind-chapter")
def bind_chapter(snapshot_id: str, body: dict = Body(...)):
    try:
        chapter_num = int(body["chapter_num"])
    except (KeyError, TypeError, ValueError):
        raise HTTPException(400, "chapter_num is required (int)")
    out = snapshot_store.bind_chapter(_db(), snapshot_id, chapter_num)
    if out is None:
        raise HTTPException(404, f"snapshot {snapshot_id} not found")
    return out


@router.post("/{snapshot_id}/unbind-chapter")
def unbind_chapter(snapshot_id: str, body: dict = Body(...)):
    try:
        chapter_num = int(body["chapter_num"])
    except (KeyError, TypeError, ValueError):
        raise HTTPException(400, "chapter_num is required (int)")
    out = snapshot_store.unbind_chapter(_db(), snapshot_id, chapter_num)
    if out is None:
        raise HTTPException(404, f"snapshot {snapshot_id} not found")
    return out


@router.post("/{snapshot_id}/mark-complete")
def mark_complete(snapshot_id: str, body: dict = Body(...)):
    try:
        chapter_num = int(body["chapter_num"])
    except (KeyError, TypeError, ValueError):
        raise HTTPException(400, "chapter_num is required (int)")
    out = snapshot_store.mark_complete(_db(), snapshot_id, chapter_num)
    if out is None:
        raise HTTPException(404, f"snapshot {snapshot_id} not found")
    return out


@router.post("/{snapshot_id}/unmark-complete")
def unmark_complete(snapshot_id: str):
    out = snapshot_store.unmark_complete(_db(), snapshot_id)
    if out is None:
        raise HTTPException(404, f"snapshot {snapshot_id} not found")
    return out


# ─────────── reminders ───────────


@reminder_router.get("")
def list_reminders(
    project_id: str = Query(...),
    chapter_num: int | None = Query(None),
):
    """List outstanding reminders. When ``chapter_num`` is supplied,
    the scanner also runs (write side) to surface any newly-overdue
    snapshots at that chapter."""
    if chapter_num is not None:
        snapshot_reminder.check_overdue_reminders(
            _db(), project_id, int(chapter_num),
        )
    return {
        "items": snapshot_reminder.list_outstanding_reminders(_db(), project_id),
    }


@reminder_router.post("/{reminder_id}/acknowledge")
def acknowledge_reminder(reminder_id: str, body: dict = Body(...)):
    action = body.get("action") or ""
    try:
        out = snapshot_reminder.acknowledge_reminder(_db(), reminder_id, action)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if out is None:
        raise HTTPException(404, f"reminder {reminder_id} not found")
    return out
