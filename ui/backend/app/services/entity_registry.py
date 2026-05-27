"""EntityRegistry — single registry of named entities a chapter can stage.

LOADER_SPEC Loader 10 prerequisite. Three sources merge here:

- ``character`` rows: every character upsert auto-syncs an entity row
  with ``source='character'`` and the character_id in ``source_ref_id``.
- ``worldbook`` rows whose category names a 地点 / 组织 / 物品 auto-sync as
  location / organization / item.
- ``manual`` rows: user-created via the entity API (planned UI).

Auto-sync hooks live in ``project_store`` (character/worldbook CRUD
paths). They wrap their work in try/except so an entity-sync failure
never bubbles up and breaks the underlying upsert.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import time
import uuid
from typing import Any

from .project_store import open_db, _row_to_dict

logger = logging.getLogger("inkoctobot.services.entity_registry")


_VALID_TYPES = {"character", "location", "item", "organization", "concept"}
_VALID_SOURCES = {"character", "worldbook", "manual"}

# worldbook category → entity_type mapping. Categories that don't map
# (规则/技术/文化/历史/自然/杂项 etc.) are intentionally skipped —
# they're worldbook *facts*, not stage-able entities.
_WORLDBOOK_CATEGORY_TO_TYPE = {
    "地点": "location",
    "place": "location",
    "组织": "organization",
    "organization": "organization",
    "factions": "organization",
    "物品": "item",
    "item": "item",
}


def _nid() -> str:
    return f"ent_{int(time.time() * 1000)}_{uuid.uuid4().hex[:6]}"


def _row_to_payload(row: sqlite3.Row) -> dict[str, Any]:
    r = _row_to_dict(row)
    return {
        "id":                 r["entity_id"],
        "project_id":         r["project_id"],
        "name":               r["name"],
        "entity_type":        r["entity_type"],
        "description":        r.get("description") or "",
        "introduced_chapter": r.get("introduced_chapter"),
        "aliases":            json.loads(r.get("aliases_json") or "[]"),
        "metadata":           json.loads(r.get("metadata_json") or "{}"),
        "source":             r.get("source") or "manual",
        "source_ref_id":      r.get("source_ref_id"),
        "created_at":         r.get("created_at"),
        "updated_at":         r.get("updated_at"),
    }


def list_entities(
    db_path: str,
    project_id: str | None = None,
    entity_type: str | None = None,
) -> list[dict[str, Any]]:
    sql = "SELECT * FROM storyland_entities"
    where: list[str] = []
    params: list[Any] = []
    if project_id:
        where.append("project_id = ?")
        params.append(project_id)
    if entity_type:
        where.append("entity_type = ?")
        params.append(entity_type)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY entity_type, name"
    with open_db(db_path) as con:
        rows = con.execute(sql, tuple(params)).fetchall()
    return [_row_to_payload(r) for r in rows]


def get_entity(db_path: str, entity_id: str) -> dict[str, Any] | None:
    with open_db(db_path) as con:
        row = con.execute(
            "SELECT * FROM storyland_entities WHERE entity_id = ?",
            (entity_id,),
        ).fetchone()
    return _row_to_payload(row) if row else None


def upsert_entity(
    db_path: str,
    body: dict[str, Any],
) -> dict[str, Any]:
    """Create or update an entity. Returns the resulting payload.

    Required fields: ``project_id``, ``name``, ``entity_type``.
    """
    pid = body.get("project_id") or ""
    name = (body.get("name") or "").strip()
    etype = (body.get("entity_type") or "").strip()
    if not pid:
        raise ValueError("project_id is required")
    if not name:
        raise ValueError("name is required")
    if etype not in _VALID_TYPES:
        raise ValueError(
            f"entity_type must be one of {sorted(_VALID_TYPES)}, got {etype!r}"
        )

    source = body.get("source") or "manual"
    if source not in _VALID_SOURCES:
        raise ValueError(
            f"source must be one of {sorted(_VALID_SOURCES)}, got {source!r}"
        )

    eid = body.get("id") or _nid()

    with open_db(db_path) as con:
        # UNIQUE(project_id, name) check.
        dup = con.execute(
            "SELECT entity_id FROM storyland_entities "
            "WHERE project_id = ? AND name = ? AND entity_id != ?",
            (pid, name, eid),
        ).fetchone()
        if dup:
            raise ValueError(f"实体「{name}」已存在")

        con.execute(
            """INSERT INTO storyland_entities (
                   entity_id, project_id, name, entity_type, description,
                   introduced_chapter, aliases_json, metadata_json,
                   source, source_ref_id,
                   created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                       CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
               ON CONFLICT(entity_id) DO UPDATE SET
                   project_id = excluded.project_id,
                   name = excluded.name,
                   entity_type = excluded.entity_type,
                   description = excluded.description,
                   introduced_chapter = excluded.introduced_chapter,
                   aliases_json = excluded.aliases_json,
                   metadata_json = excluded.metadata_json,
                   source = excluded.source,
                   source_ref_id = excluded.source_ref_id,
                   updated_at = CURRENT_TIMESTAMP""",
            (eid, pid, name, etype,
             body.get("description") or "",
             body.get("introduced_chapter"),
             json.dumps(body.get("aliases") or [], ensure_ascii=False),
             json.dumps(body.get("metadata") or {}, ensure_ascii=False),
             source,
             body.get("source_ref_id")),
        )
        con.commit()
    out = get_entity(db_path, eid)
    if out is None:  # pragma: no cover
        raise RuntimeError("upsert succeeded but row not found")
    return out


def delete_entity(db_path: str, entity_id: str) -> None:
    with open_db(db_path) as con:
        con.execute(
            "DELETE FROM storyland_entities WHERE entity_id = ?",
            (entity_id,),
        )
        con.commit()


def _delete_by_source_ref(db_path: str, source: str, source_ref_id: str) -> None:
    """Internal helper used by character / worldbook delete hooks."""
    with open_db(db_path) as con:
        con.execute(
            "DELETE FROM storyland_entities "
            "WHERE source = ? AND source_ref_id = ?",
            (source, source_ref_id),
        )
        con.commit()


def _find_by_source_ref(
    db_path: str, source: str, source_ref_id: str,
) -> str | None:
    with open_db(db_path) as con:
        row = con.execute(
            "SELECT entity_id FROM storyland_entities "
            "WHERE source = ? AND source_ref_id = ?",
            (source, source_ref_id),
        ).fetchone()
    return row["entity_id"] if row else None


# ─────────── auto-sync hooks for character / worldbook CRUD ───────


def sync_from_character(db_path: str, character: dict[str, Any]) -> None:
    """Mirror a characters-table row into storyland_entities.

    Called by ``project_store.upsert_character``. Failures are swallowed
    with a warn-log: an entity-sync hiccup must never break the user's
    character save.
    """
    try:
        cid = character.get("id")
        pid = character.get("project_id")
        name = (character.get("name") or "").strip()
        if not (cid and pid and name):
            return
        eid = _find_by_source_ref(db_path, "character", cid)
        upsert_entity(db_path, {
            "id":            eid,
            "project_id":    pid,
            "name":          name,
            "entity_type":   "character",
            "description":   character.get("background") or character.get("description") or "",
            "aliases":       character.get("aliases") or [],
            "source":        "character",
            "source_ref_id": cid,
        })
    except Exception as e:
        logger.warning("character→entity sync failed for %s: %s",
                       character.get("name"), e)


def sync_from_worldbook(db_path: str, entry: dict[str, Any]) -> None:
    """Mirror a worldbook entry into storyland_entities when its category
    names a stage-able entity (地点/组织/物品).

    Other categories (规则/技术/...) are intentionally skipped — they're
    facts, not stage-able entities. Any pre-existing entity for the
    same source_ref is left as-is so the loader keeps a stable id.
    """
    try:
        eid_existing = _find_by_source_ref(db_path, "worldbook", entry.get("id") or "")
        category = (entry.get("category") or "").strip()
        target_type = _WORLDBOOK_CATEGORY_TO_TYPE.get(category)
        if target_type is None:
            # Category isn't entity-worthy; if we had an entity for an
            # earlier entity-worthy version, drop it.
            if eid_existing:
                delete_entity(db_path, eid_existing)
            return
        pid = entry.get("project_id")
        name = (entry.get("title") or "").strip()
        if not (pid and name):
            return
        upsert_entity(db_path, {
            "id":            eid_existing,
            "project_id":    pid,
            "name":          name,
            "entity_type":   target_type,
            "description":   entry.get("content") or "",
            "source":        "worldbook",
            "source_ref_id": entry.get("id"),
        })
    except Exception as e:
        logger.warning("worldbook→entity sync failed for %s: %s",
                       entry.get("title"), e)


def sync_delete_character(db_path: str, character_id: str) -> None:
    try:
        _delete_by_source_ref(db_path, "character", character_id)
    except Exception as e:
        logger.warning("character entity delete failed for %s: %s",
                       character_id, e)


def sync_delete_worldbook(db_path: str, entry_id: str) -> None:
    try:
        _delete_by_source_ref(db_path, "worldbook", entry_id)
    except Exception as e:
        logger.warning("worldbook entity delete failed for %s: %s",
                       entry_id, e)
