"""FastAPI router: Project CRUD + export."""
from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from ui.backend.app.settings import settings

router = APIRouter(prefix="/api/projects", tags=["projects"])
logger = logging.getLogger("inkoctobot.ui.backend.project_api")


def _db_path() -> str:
    return str(settings.repo_root / "data" / "webnovel.db")


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(_db_path())
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    return c


class ProjectCreate(BaseModel):
    title: str
    genre: str = ""
    logline: str = ""
    model_preset: str = "balanced"


class ProjectUpdate(BaseModel):
    title: str | None = None
    genre: str | None = None
    logline: str | None = None
    status: str | None = None
    model_preset: str | None = None
    style_profile_json: str | None = None


@router.get("/status")
def projects_status():
    return {"status": "ok", "router": "projects"}


@router.get("")
def list_projects(
    status: str | None = None,
    limit: int = Query(default=50, le=200),
    offset: int = Query(default=0, ge=0),
):
    try:
        with _conn() as c:
            if status:
                rows = c.execute(
                    "SELECT * FROM projects WHERE status=? ORDER BY updated_at DESC LIMIT ? OFFSET ?",
                    (status, limit, offset),
                ).fetchall()
            else:
                rows = c.execute(
                    "SELECT * FROM projects ORDER BY updated_at DESC LIMIT ? OFFSET ?",
                    (limit, offset),
                ).fetchall()
        return {"projects": [dict(r) for r in rows]}
    except Exception as e:
        return {"projects": [], "error": str(e)}


@router.post("")
def create_project(req: ProjectCreate):
    pid = f"proj_{uuid.uuid4().hex[:12]}"
    try:
        from database.creation_schema import ensure_creation_tables
        with _conn() as c:
            ensure_creation_tables(c)
            c.execute(
                """INSERT INTO projects (project_id, title, genre, logline, model_preset)
                   VALUES (?, ?, ?, ?, ?)""",
                (pid, req.title, req.genre, req.logline, req.model_preset),
            )
            c.commit()
        # Create project directory
        from security.data_isolation import DataIsolation
        iso = DataIsolation(str(settings.repo_root / "data" / "projects"))
        iso.create_project_dir(pid)
        return {"status": "ok", "project_id": pid}
    except Exception as e:
        logger.error("Create project error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{project_id}")
def get_project(project_id: str):
    with _conn() as c:
        row = c.execute(
            "SELECT * FROM projects WHERE project_id=?", (project_id,),
        ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Project not found")
    return dict(row)


@router.patch("/{project_id}")
def update_project(project_id: str, req: ProjectUpdate):
    sets = ["updated_at=CURRENT_TIMESTAMP"]
    params: list[Any] = []
    for field, val in req.model_dump(exclude_none=True).items():
        sets.append(f"{field}=?")
        params.append(val)
    if len(sets) == 1:
        return get_project(project_id)
    params.append(project_id)
    with _conn() as c:
        c.execute(f"UPDATE projects SET {', '.join(sets)} WHERE project_id=?", params)
        c.commit()
    return get_project(project_id)


@router.delete("/{project_id}")
def delete_project(project_id: str):
    with _conn() as c:
        cur = c.execute("DELETE FROM projects WHERE project_id=?", (project_id,))
        c.commit()
    if cur.rowcount == 0:
        raise HTTPException(status_code=404, detail="Project not found")
    from security.data_isolation import DataIsolation
    iso = DataIsolation(str(settings.repo_root / "data" / "projects"))
    iso.cleanup_project(project_id, keep_exports=False)
    return {"status": "ok", "deleted": project_id}


@router.post("/{project_id}/export")
def export_project(project_id: str):
    from security.data_isolation import DataIsolation
    iso = DataIsolation(str(settings.repo_root / "data" / "projects"))
    try:
        output = settings.repo_root / "data" / "projects" / project_id / "exports" / f"{project_id}_export"
        result = iso.export_project(project_id, output)
        return {"status": "ok", "path": str(result)}
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Project not found")
