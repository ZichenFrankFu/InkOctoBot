"""Project ⇄ reference-work links.

Tracks which reference works a project has imported and along what
"dimension" (style / characters / plot / rhythm). Links can target
specific entries (e.g. "I want THIS chapter as a style reference")
and can name the referenced character that the project's character
is patterned after.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel

from ._common import db

router = APIRouter()


class LinkCreate(BaseModel):
    project_id: str
    ref_id: str
    dimension: str
    entry_ids: list[str] = []
    reference_character_name: Optional[str] = None
    notes: Optional[str] = None


@router.post("/links")
def create_link(body: LinkCreate):
    return db().link_to_project(
        body.project_id, body.ref_id, body.dimension,
        entry_ids=body.entry_ids,
        reference_character_name=body.reference_character_name,
        notes=body.notes,
    )


@router.get("/links/{project_id}")
def get_links(project_id: str):
    return {"items": db().get_project_links(project_id)}
