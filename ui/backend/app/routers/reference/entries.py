"""Reference entries CRUD.

An entry is a structured slice of a reference work — a quoted passage,
a character note, an outline chunk — attached to its parent work via
``ref_id`` and tagged with an ``entry_type``.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ._common import db

router = APIRouter()


class EntryCreate(BaseModel):
    ref_id: str
    entry_type: str = "other"
    title: str = ""
    content: str = ""
    content_source: str = "user_written"
    position_label: str = ""
    user_notes: str = ""
    learning_dimensions: list[str] = []
    user_rating: Optional[int] = None
    tags: list[str] = []


@router.get("/entries/{ref_id}")
def list_entries(ref_id: str, entry_type: Optional[str] = None):
    return {"items": db().list_entries(ref_id, entry_type)}


@router.post("/entries")
def create_entry(body: EntryCreate):
    return db().add_entry(
        ref_id=body.ref_id, entry_type=body.entry_type,
        content=body.content, title=body.title,
        content_source=body.content_source,
        position_label=body.position_label,
        user_notes=body.user_notes,
        learning_dimensions=body.learning_dimensions,
        user_rating=body.user_rating, tags=body.tags,
    )


@router.delete("/entries/{entry_id}")
def delete_entry(entry_id: str):
    if not db().delete_entry(entry_id):
        raise HTTPException(404, "not found")
    return {"ok": True}
