"""Deprecated router.

``/api/projects`` was an earlier CRUD surface that pointed at the wrong
database file (``webnovel.db`` instead of ``novels.db``), had path
traversal bugs in its export/delete paths, and bypassed
``services.project_paths.get_db_path`` so it never honored test mode.

It was never wired to the frontend (the React app uses ``/api/data/projects``
from json_storage_api, which goes through project_store and is DB-backed).

All endpoints now return HTTP 410 Gone with a pointer to the canonical
path. Kept as a router so older curl scripts get a clear error rather
than 404.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/api/projects", tags=["projects"])
logger = logging.getLogger("inkoctobot.ui.backend.project_api")


_GONE_MESSAGE = (
    "/api/projects is deprecated and unused. Use /api/data/projects "
    "(json_storage_api → project_store, DB-backed)."
)


def _gone() -> None:
    raise HTTPException(status_code=410, detail=_GONE_MESSAGE)


@router.get("/status")
def projects_status():
    return {"status": "deprecated", "see": "/api/data/projects"}


@router.get("")
def list_projects():
    _gone()


@router.post("")
def create_project():
    _gone()


@router.get("/{project_id}")
def get_project(project_id: str):
    _gone()


@router.patch("/{project_id}")
def update_project(project_id: str):
    _gone()


@router.delete("/{project_id}")
def delete_project(project_id: str):
    _gone()


@router.post("/{project_id}/export")
def export_project(project_id: str):
    _gone()
