"""/api/storyland — Storyland 页面数据面 (spec 6.4.5).

Read / manage surfaces the three-tab Storyland page needs beyond the
existing routers (/api/entities, /api/state-review, /api/genesis,
/api/data/foreshadowing):

- SPO facts by story timeline or by chapter (Tab 1)
- subplot threads list + CRUD (Tab 3 主线/支线)
- hook (伏笔) create / edit with the Phase-1 scale field (Tab 3)
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from typing import Any

from fastapi import APIRouter, Body, HTTPException, Query

router = APIRouter(prefix="/api/storyland", tags=["storyland"])

_HOOK_SCALES = {"boomerang", "event_clue", "grand_plan", "world_truth"}
_THREAD_STATUSES = {"setup", "building", "climax", "resolution", "dormant"}


def _db() -> str:
    from ..services.project_paths import get_db_path
    return get_db_path()


def _rows(con: sqlite3.Connection, sql: str, args: tuple) -> list[dict]:
    con.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in con.execute(sql, args).fetchall()]
    except sqlite3.OperationalError:
        return []


# ─────────── Tab 1: SPO facts ───────────

@router.get("/state")
def list_state(
    project_id: str = Query(...),
    chapter_num: int | None = Query(default=None),
    subject: str = Query(default=""),
    include_closed: bool = Query(default=False),
):
    """SPO facts. ``chapter_num`` filters to facts valid AT that
    chapter (按章节视图); otherwise the full timeline (按时间线,
    ordered by valid_from_chapter)."""
    where = ["project_id = ?"]
    args: list[Any] = [project_id]
    if subject:
        where.append("subject = ?")
        args.append(subject)
    if chapter_num is not None:
        where.append("valid_from_chapter <= ?")
        args.append(chapter_num)
        where.append("(valid_to_chapter IS NULL OR valid_to_chapter >= ?)")
        args.append(chapter_num)
    elif not include_closed:
        where.append("valid_to_chapter IS NULL")
    with sqlite3.connect(_db()) as con:
        items = _rows(
            con,
            "SELECT triple_id, subject, subject_type, predicate, object, "
            "valid_from_chapter, valid_to_chapter, confidence "
            f"FROM truth_current_state WHERE {' AND '.join(where)} "
            "ORDER BY valid_from_chapter, subject",
            tuple(args),
        )
    return {"items": items}


# ─────────── Tab 3: subplot threads (主线/支线) ───────────

@router.get("/subplots")
def list_subplots(project_id: str = Query(...)):
    with sqlite3.connect(_db()) as con:
        items = _rows(
            con,
            "SELECT thread_id, name, description, status, thread_type, "
            "start_chapter, last_advanced_chapter, related_hook_ids_json "
            "FROM subplot_threads WHERE project_id = ? "
            "ORDER BY thread_type DESC, start_chapter",
            (project_id,),
        )
    for it in items:
        try:
            it["related_hook_ids"] = json.loads(
                it.pop("related_hook_ids_json") or "[]")
        except Exception:
            it["related_hook_ids"] = []
    return {"items": items}


@router.post("/subplots")
def create_subplot(body: dict = Body(...)):
    project_id = (body.get("project_id") or "").strip()
    name = (body.get("name") or "").strip()
    if not project_id or not name:
        raise HTTPException(400, "project_id and name required")
    thread_type = body.get("thread_type") or "sub"
    if thread_type not in ("main", "sub"):
        raise HTTPException(400, "thread_type must be main/sub")
    with sqlite3.connect(_db()) as con:
        # 故事线·机制1: 一个项目有且仅有一条主线。
        if thread_type == "main":
            dup = con.execute(
                "SELECT 1 FROM subplot_threads "
                "WHERE project_id = ? AND thread_type = 'main'",
                (project_id,),
            ).fetchone()
            if dup:
                raise HTTPException(409, "项目已存在主线（有且仅有一条）")
        tid = f"th_{uuid.uuid4().hex[:12]}"
        con.execute(
            "INSERT INTO subplot_threads (thread_id, project_id, name, "
            "description, status, thread_type, start_chapter) "
            "VALUES (?, ?, ?, ?, 'setup', ?, ?)",
            (tid, project_id, name,
             (body.get("description") or "").strip(),
             thread_type, int(body.get("start_chapter") or 1)),
        )
        con.commit()
    return {"ok": True, "thread_id": tid}


@router.put("/subplots/{thread_id}")
def update_subplot(thread_id: str, body: dict = Body(...)):
    sets: list[str] = []
    args: list[Any] = []
    if "name" in body:
        name = (body.get("name") or "").strip()
        if not name:
            raise HTTPException(400, "name cannot be empty")
        sets.append("name = ?")
        args.append(name)
    if "description" in body:
        sets.append("description = ?")
        args.append((body.get("description") or "").strip())
    if "status" in body:
        if body["status"] not in _THREAD_STATUSES:
            raise HTTPException(400, f"status must be one of {sorted(_THREAD_STATUSES)}")
        sets.append("status = ?")
        args.append(body["status"])
    if not sets:
        raise HTTPException(400, "nothing to update")
    sets.append("updated_at = CURRENT_TIMESTAMP")
    with sqlite3.connect(_db()) as con:
        cur = con.execute(
            f"UPDATE subplot_threads SET {', '.join(sets)} "
            f"WHERE thread_id = ?",
            (*args, thread_id),
        )
        con.commit()
    if cur.rowcount == 0:
        raise HTTPException(404, "thread not found")
    return {"ok": True, "thread_id": thread_id}


@router.delete("/subplots/{thread_id}")
def delete_subplot(thread_id: str):
    with sqlite3.connect(_db()) as con:
        cur = con.execute(
            "DELETE FROM subplot_threads WHERE thread_id = ?", (thread_id,),
        )
        con.commit()
    if cur.rowcount == 0:
        raise HTTPException(404, "thread not found")
    return {"ok": True}


# ─────────── Tab 3: hooks (伏笔) create / edit ───────────

@router.post("/hooks")
def create_hook(body: dict = Body(...)):
    """User-created hook with the v3.1 规模 field; the payoff window
    derives from scale unless an explicit expected chapter is given
    (故事线·机制4)."""
    project_id = (body.get("project_id") or "").strip()
    description = (body.get("description") or "").strip()
    if not project_id or not description:
        raise HTTPException(400, "project_id and description required")
    scale = body.get("scale") or "event_clue"
    if scale not in _HOOK_SCALES:
        raise HTTPException(400, f"scale must be one of {sorted(_HOOK_SCALES)}")
    origin = int(body.get("origin_chapter") or 1)
    expected = body.get("expected_payoff_chapter")
    if expected is None:
        from knowledge.storyland_state.schemas import HOOK_SCALE_WINDOWS
        window = HOOK_SCALE_WINDOWS.get(scale)
        expected = origin + window if window is not None else None
    hook_id = f"hk_{uuid.uuid4().hex[:12]}"
    with sqlite3.connect(_db()) as con:
        con.execute(
            "INSERT INTO pending_hooks (hook_id, project_id, description, "
            "status, importance, scale, origin_chapter, "
            "expected_payoff_chapter, last_mention_chapter, "
            "last_advance_chapter, pressure_threshold) "
            "VALUES (?, ?, ?, 'open', ?, ?, ?, ?, ?, ?, 5)",
            (hook_id, project_id, description,
             body.get("importance") or "B", scale, origin, expected,
             origin, origin),
        )
        con.execute(
            "INSERT INTO hook_events (event_id, hook_id, project_id, "
            "chapter_num, action, before_status, after_status, evidence) "
            "VALUES (?, ?, ?, ?, 'new', NULL, 'open', 'user created')",
            (f"he_{uuid.uuid4().hex[:12]}", hook_id, project_id, origin),
        )
        con.commit()
    return {"ok": True, "hook_id": hook_id,
            "expected_payoff_chapter": expected}


@router.put("/hooks/{hook_id}")
def update_hook(hook_id: str, body: dict = Body(...)):
    sets: list[str] = []
    args: list[Any] = []
    if "description" in body:
        desc = (body.get("description") or "").strip()
        if not desc:
            raise HTTPException(400, "description cannot be empty")
        sets.append("description = ?")
        args.append(desc)
    if "scale" in body:
        if body["scale"] not in _HOOK_SCALES:
            raise HTTPException(400, f"scale must be one of {sorted(_HOOK_SCALES)}")
        sets.append("scale = ?")
        args.append(body["scale"])
    if "expected_payoff_chapter" in body:
        sets.append("expected_payoff_chapter = ?")
        v = body["expected_payoff_chapter"]
        args.append(int(v) if v is not None else None)
    if "importance" in body:
        if body["importance"] not in ("A", "B", "C"):
            raise HTTPException(400, "importance must be A/B/C")
        sets.append("importance = ?")
        args.append(body["importance"])
    if not sets:
        raise HTTPException(400, "nothing to update")
    sets.append("updated_at = CURRENT_TIMESTAMP")
    with sqlite3.connect(_db()) as con:
        cur = con.execute(
            f"UPDATE pending_hooks SET {', '.join(sets)} WHERE hook_id = ?",
            (*args, hook_id),
        )
        con.commit()
    if cur.rowcount == 0:
        raise HTTPException(404, "hook not found")
    return {"ok": True, "hook_id": hook_id}


@router.delete("/hooks/{hook_id}")
def delete_hook(hook_id: str):
    with sqlite3.connect(_db()) as con:
        cur = con.execute(
            "DELETE FROM pending_hooks WHERE hook_id = ?", (hook_id,),
        )
        con.commit()
    if cur.rowcount == 0:
        raise HTTPException(404, "hook not found")
    return {"ok": True}
