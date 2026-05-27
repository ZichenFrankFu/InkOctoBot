"""Entity registry API — CRUD on storyland_entities.

LOADER_SPEC Loader 10. Mounted at /api/entities so the prefix matches
the spec exactly (most other v3 endpoints in the LOADER_SPEC use the
same /api/<resource> convention).

Auto-synced rows (source='character' or 'worldbook') accept edits
through this endpoint too — the writer is the entity registry, not
the underlying character/worldbook tables — but a subsequent character
or worldbook upsert will overwrite the description back to the source
value. UI is expected to surface the source so users know what's
authoritative.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Body, HTTPException, Query

from ..services import entity_registry, project_paths

router = APIRouter(prefix="/api/entities", tags=["entities"])

logger = logging.getLogger("inkoctobot.routers.entity_api")


def _db() -> str:
    return project_paths.get_db_path()


@router.get("")
def list_entities(
    project_id: str | None = Query(None),
    entity_type: str | None = Query(None),
):
    return {
        "items": entity_registry.list_entities(_db(), project_id, entity_type),
    }


@router.get("/{entity_id}")
def get_entity(entity_id: str):
    ent = entity_registry.get_entity(_db(), entity_id)
    if ent is None:
        raise HTTPException(status_code=404, detail=f"entity {entity_id} not found")
    return ent


@router.post("")
def create_entity(body: dict = Body(...)):
    try:
        return entity_registry.upsert_entity(_db(), body)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.put("/{entity_id}")
def update_entity(entity_id: str, body: dict = Body(...)):
    body = {**body, "id": entity_id}
    try:
        return entity_registry.upsert_entity(_db(), body)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/{entity_id}")
def delete_entity(entity_id: str):
    entity_registry.delete_entity(_db(), entity_id)
    return {"ok": True}
