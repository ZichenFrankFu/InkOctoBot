"""Inspiration library.

A personal store of free-text idea snippets (scenes / plot devices /
character designs / …). Each entry can be used as a query to fuzzy-
search the reference works via the existing /search endpoint.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ._common import idea_db

router = APIRouter()


class InspirationCreate(BaseModel):
    category: str = "other"
    title: str = ""
    content: str


class InspirationUpdate(BaseModel):
    category: Optional[str] = None
    title: Optional[str] = None
    content: Optional[str] = None


@router.get("/inspirations")
def list_inspirations():
    """All inspirations, newest-updated first."""
    return {"items": idea_db().list_inspirations()}


@router.post("/inspirations")
def create_inspiration(body: InspirationCreate):
    content = (body.content or "").strip()
    if not content:
        raise HTTPException(400, "灵感内容不能为空")
    return idea_db().create_inspiration(
        (body.category or "other").strip() or "other",
        (body.title or "").strip(),
        content,
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
    return idea_db().update_inspiration(insp_id, **fields)


@router.delete("/inspirations/{insp_id}")
def delete_inspiration(insp_id: str):
    if not idea_db().delete_inspiration(insp_id):
        raise HTTPException(404, "灵感不存在")
    return {"ok": True}
