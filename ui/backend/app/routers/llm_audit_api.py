"""Unified audit view over every LLM call (``llm_outputs`` table).

Endpoints:
    GET  /api/llm-audit                       — filtered list
    GET  /api/llm-audit/{output_id}            — one row
    POST /api/llm-audit/{output_id}/acknowledge  — user 'reviewed'
    POST /api/llm-audit/{output_id}/mark-corrected — user 'corrected'
    GET  /api/llm-audit/stats                  — counts by call_site / source
"""
from __future__ import annotations

import logging
import sqlite3
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from ..services.project_paths import get_db_path

logger = logging.getLogger("inkoctobot.routers.llm_audit_api")


router = APIRouter(prefix="/api/llm-audit", tags=["llm-audit"])


def _row_to_dict(r: sqlite3.Row) -> dict:
    return dict(r)


@router.get("")
def list_outputs(
    call_site_id: Optional[str] = Query(default=None),
    project_id: Optional[str] = Query(default=None),
    chapter_id: Optional[str] = Query(default=None),
    source: Optional[str] = Query(default=None),
    unacknowledged_only: bool = Query(default=False),
    limit: int = Query(default=50, le=500),
) -> dict:
    where: list[str] = []
    params: list = []
    if call_site_id:
        where.append("call_site_id = ?"); params.append(call_site_id)
    if project_id:
        where.append("project_id = ?"); params.append(project_id)
    if chapter_id:
        where.append("chapter_id = ?"); params.append(chapter_id)
    if source:
        if source not in ("auto", "manual"):
            raise HTTPException(400, "source must be 'auto' or 'manual'")
        where.append("source = ?"); params.append(source)
    if unacknowledged_only:
        where.append("user_acknowledged = 0")
    sql = "SELECT * FROM llm_outputs"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)
    with sqlite3.connect(get_db_path()) as con:
        con.row_factory = sqlite3.Row
        rows = [_row_to_dict(r) for r in con.execute(sql, params)]
    return {"outputs": rows, "count": len(rows)}


@router.get("/stats")
def output_stats() -> dict:
    """Counts grouped by call_site_id and by source for the dashboard."""
    with sqlite3.connect(get_db_path()) as con:
        by_site = dict(con.execute(
            "SELECT call_site_id, COUNT(*) FROM llm_outputs "
            "GROUP BY call_site_id"
        ).fetchall())
        by_source = dict(con.execute(
            "SELECT source, COUNT(*) FROM llm_outputs GROUP BY source"
        ).fetchall())
        unacked = con.execute(
            "SELECT COUNT(*) FROM llm_outputs WHERE user_acknowledged = 0"
        ).fetchone()[0]
    return {
        "by_call_site": by_site,
        "by_source":    by_source,
        "unacknowledged": unacked,
    }


@router.get("/{output_id}")
def get_output(output_id: str) -> dict:
    with sqlite3.connect(get_db_path()) as con:
        con.row_factory = sqlite3.Row
        row = con.execute(
            "SELECT * FROM llm_outputs WHERE output_id = ?",
            (output_id,),
        ).fetchone()
    if row is None:
        raise HTTPException(404, f"output {output_id!r} not found")
    return _row_to_dict(row)


@router.post("/{output_id}/acknowledge")
def acknowledge(output_id: str) -> dict:
    with sqlite3.connect(get_db_path()) as con:
        cur = con.execute(
            "UPDATE llm_outputs SET user_acknowledged = 1 WHERE output_id = ?",
            (output_id,),
        )
        con.commit()
    if cur.rowcount == 0:
        raise HTTPException(404, f"output {output_id!r} not found")
    return {"ok": True, "output_id": output_id}


@router.post("/{output_id}/mark-corrected")
def mark_corrected(output_id: str) -> dict:
    with sqlite3.connect(get_db_path()) as con:
        cur = con.execute(
            "UPDATE llm_outputs "
            "SET user_acknowledged = 1, user_corrected = 1 "
            "WHERE output_id = ?",
            (output_id,),
        )
        con.commit()
    if cur.rowcount == 0:
        raise HTTPException(404, f"output {output_id!r} not found")
    return {"ok": True, "output_id": output_id, "user_corrected": True}
