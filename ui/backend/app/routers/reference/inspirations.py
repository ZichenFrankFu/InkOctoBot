"""Inspiration library.

A personal store of free-text idea snippets (scenes / plot devices /
character designs / …). Each entry can be used as a query to fuzzy-
search the reference works via the existing /search endpoint.

LOADER_SPEC Loader 4 (Batch 3) extensions:
- Optional ``project_id`` on every row. ``project_id IS NULL`` means
  "global" — visible to every project. The list endpoint with a
  ``project_id`` query param returns global rows + the project's own.
- Optional ``tags`` array alongside the legacy ``category`` string.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from ._common import idea_db

router = APIRouter()


class InspirationCreate(BaseModel):
    category: str = "other"
    title: str = ""
    content: str
    project_id: Optional[str] = None
    tags: Optional[list[str]] = None


class InspirationUpdate(BaseModel):
    category: Optional[str] = None
    title: Optional[str] = None
    content: Optional[str] = None
    project_id: Optional[str] = None
    tags: Optional[list[str]] = None


@router.get("/inspirations/stats")
def inspiration_stats(project_id: Optional[str] = Query(None)):
    """灵感管理页面事实行 (spec 6.3): 总数 / 已生成 embedding 数 /
    已被章节使用数 / 类别分布。"""
    import json as _json
    import sqlite3
    from ._common import idea_db_path
    with sqlite3.connect(idea_db_path()) as con:
        con.row_factory = sqlite3.Row
        try:
            where = ""
            args: tuple = ()
            if project_id:
                where = "WHERE project_id IS NULL OR project_id = ?"
                args = (project_id,)
            rows = con.execute(
                f"SELECT category, embedding_json, used_in_chapters_json "
                f"FROM inspirations {where}",
                args,
            ).fetchall()
        except sqlite3.OperationalError:
            return {"total": 0, "embedded": 0, "used": 0, "by_category": {}}
    total = len(rows)
    embedded = 0
    used = 0
    by_category: dict[str, int] = {}
    for r in rows:
        try:
            if _json.loads(r["embedding_json"] or "[]"):
                embedded += 1
        except Exception:
            pass
        try:
            if _json.loads(r["used_in_chapters_json"] or "[]"):
                used += 1
        except Exception:
            pass
        cat = r["category"] or "other"
        by_category[cat] = by_category.get(cat, 0) + 1
    return {"total": total, "embedded": embedded, "used": used,
            "by_category": by_category}


@router.get("/inspirations")
def list_inspirations(project_id: Optional[str] = Query(None)):
    """List inspirations newest-updated first.

    When ``project_id`` is provided, returns global (NULL ``project_id``)
    rows plus the project's own. Without ``project_id``, returns every
    row (legacy behavior).
    """
    return {"items": idea_db().list_inspirations(project_id=project_id)}


@router.post("/inspirations")
def create_inspiration(body: InspirationCreate):
    content = (body.content or "").strip()
    if not content:
        raise HTTPException(400, "灵感内容不能为空")
    return idea_db().create_inspiration(
        (body.category or "other").strip() or "other",
        (body.title or "").strip(),
        content,
        project_id=body.project_id,
        tags=body.tags,
    )


@router.put("/inspirations/{insp_id}")
def update_inspiration(insp_id: str, body: InspirationUpdate):
    if not idea_db().get_inspiration(insp_id):
        raise HTTPException(404, "灵感不存在")
    fields: dict = {}
    if body.category is not None:
        fields["category"] = body.category.strip() or "other"
    if body.title is not None:
        fields["title"] = body.title.strip()
    if body.content is not None:
        content = body.content.strip()
        if not content:
            raise HTTPException(400, "灵感内容不能为空")
        fields["content"] = content
    if body.project_id is not None:
        fields["project_id"] = body.project_id or None
    if body.tags is not None:
        fields["tags"] = list(body.tags)
    return idea_db().update_inspiration(insp_id, **fields)


@router.delete("/inspirations/{insp_id}")
def delete_inspiration(insp_id: str):
    if not idea_db().delete_inspiration(insp_id):
        raise HTTPException(404, "灵感不存在")
    return {"ok": True}
