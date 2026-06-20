"""
project_store.py — DB-backed CRUD for per-project entities.

Replaces the JSON-file storage used by ``routers/json_storage_api.py``
for the following collections (v2 schema; see docs/SCHEMA_REDESIGN.md):
  - characters         (characters table)
  - worldbook          (worldbook_entries table)
  - project_memory     (project_memories table)

Each adapter exposes a small functional API:
  - ``list_items(project_id=...)`` -> list[dict]
  - ``get(item_id)`` -> dict | None
  - ``upsert(item_id, body)`` -> dict
  - ``delete(item_id)`` -> None

Wire shape matches the legacy JSON dict shape so frontend / loaders
don't have to change. Each adapter is stateless; pass the db path in.
"""
from __future__ import annotations

import json
import sqlite3
import time
import uuid
from typing import Any

from storage.connection import get_conn as _get_conn

# Track DBs we've already initialized so we only call ensure_creation_tables
# once per path per process.
_initialized: set[str] = set()


def open_db(db_path: str):
    """Open a connection with PRAGMAs applied. First call per path also
    materializes the project schema."""
    if db_path not in _initialized:
        with _get_conn(db_path) as con:
            from storage.project_schema import ensure_creation_tables
            ensure_creation_tables(con)
        _initialized.add(db_path)
    return _get_conn(db_path)


def _nid(prefix: str = "") -> str:
    return f"{prefix}{int(time.time() * 1000)}_{uuid.uuid4().hex[:6]}"


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {k: row[k] for k in row.keys()}


# ─────────────── Characters ────────────────────────────────────────


_CHARACTER_JSON_COLS = {"tags_json", "relationships_json",
                        "layer_a_json", "layer_b_json", "extra_json"}


def _character_row_to_payload(row: sqlite3.Row) -> dict[str, Any]:
    """SQLite row -> the JSON-file dict shape the frontend expects."""
    r = _row_to_dict(row)
    out: dict[str, Any] = {
        "id": r["character_id"],
        "project_id": r["project_id"],
        "name": r["name"],
        "role": r.get("role") or "",
        "description": r.get("description") or "",
        "personality": r.get("personality") or "",
        "background": r.get("background") or "",
        "appearance": r.get("appearance") or "",
        "speech_style": r.get("speech_style") or "",
        "sort_order": r.get("sort_order") or 0,
        "created_at": r.get("created_at"),
        "updated_at": r.get("updated_at"),
    }
    out["tags"] = json.loads(r.get("tags_json") or "[]")
    out["relationships"] = json.loads(r.get("relationships_json") or "[]")
    out["layer_a"] = json.loads(r.get("layer_a_json") or "{}")
    out["layer_b"] = json.loads(r.get("layer_b_json") or "{}")
    # Restore any unknown fields stashed in extra_json so the round-trip
    # preserves frontend-only keys that the schema doesn't model.
    extra = json.loads(r.get("extra_json") or "{}") or {}
    for k, v in extra.items():
        if k not in out:
            out[k] = v
    return out


def list_characters(db_path: str, project_id: str | None = None) -> list[dict]:
    sql = (
        "SELECT * FROM characters"
        + (" WHERE project_id = ?" if project_id else "")
        + " ORDER BY sort_order, name"
    )
    params = (project_id,) if project_id else ()
    with open_db(db_path) as con:
        rows = con.execute(sql, params).fetchall()
    return [_character_row_to_payload(r) for r in rows]


def get_character(db_path: str, character_id: str) -> dict | None:
    with open_db(db_path) as con:
        row = con.execute(
            "SELECT * FROM characters WHERE character_id = ?",
            (character_id,),
        ).fetchone()
    return _character_row_to_payload(row) if row else None


def upsert_character(db_path: str, body: dict[str, Any]) -> dict[str, Any]:
    """Insert or update a character. Returns the resulting payload.

    Raises ``ValueError`` on UNIQUE violation (duplicate name in project).
    """
    cid = body.get("id") or _nid("char_")
    pid = body.get("project_id") or ""
    name = (body.get("name") or "").strip()
    if not name:
        raise ValueError("name is required")
    if not pid:
        raise ValueError("project_id is required")

    known_cols = {
        "id", "project_id", "name", "role", "description", "personality",
        "background", "appearance", "speech_style", "sort_order",
        "tags", "relationships", "layer_a", "layer_b",
        "created_at", "updated_at",
    }
    # ``dynamic_snapshots`` is owned at runtime by snapshot_store (its own
    # table) for the canonical generation pipeline, but the legacy 角色卡
    # UI still reads/writes this field directly. We let it pass through
    # into ``extra_json`` so the snapshot card survives a reload; the
    # canonical snapshot_store remains the source of truth for the
    # generation pipeline and is unaffected. A future migration can move
    # this back into the table without changing this code path.
    extra = {k: v for k, v in body.items() if k not in known_cols}

    with open_db(db_path) as con:
        # Check UNIQUE (project_id, name) for OTHER ids.
        dup = con.execute(
            "SELECT character_id FROM characters "
            "WHERE project_id = ? AND name = ? AND character_id != ?",
            (pid, name, cid),
        ).fetchone()
        if dup:
            raise ValueError(f"角色「{name}」已存在，请使用不同的名字")

        con.execute(
            """INSERT INTO characters (
                   character_id, project_id, name, role, description,
                   personality, background, appearance, speech_style,
                   tags_json, relationships_json, layer_a_json,
                   layer_b_json, extra_json, sort_order,
                   created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                       CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
               ON CONFLICT(character_id) DO UPDATE SET
                   project_id = excluded.project_id,
                   name = excluded.name,
                   role = excluded.role,
                   description = excluded.description,
                   personality = excluded.personality,
                   background = excluded.background,
                   appearance = excluded.appearance,
                   speech_style = excluded.speech_style,
                   tags_json = excluded.tags_json,
                   relationships_json = excluded.relationships_json,
                   layer_a_json = excluded.layer_a_json,
                   layer_b_json = excluded.layer_b_json,
                   extra_json = excluded.extra_json,
                   sort_order = excluded.sort_order,
                   updated_at = CURRENT_TIMESTAMP""",
            (cid, pid, name,
             body.get("role", ""),
             body.get("description", ""),
             body.get("personality", ""),
             body.get("background", ""),
             body.get("appearance", ""),
             body.get("speech_style", ""),
             json.dumps(body.get("tags", []), ensure_ascii=False),
             json.dumps(body.get("relationships", []), ensure_ascii=False),
             json.dumps(body.get("layer_a", {}), ensure_ascii=False),
             json.dumps(body.get("layer_b", {}), ensure_ascii=False),
             json.dumps(extra, ensure_ascii=False),
             int(body.get("sort_order", 0) or 0)),
        )
        con.commit()
    saved = get_character(db_path, cid)
    if saved is None:  # pragma: no cover — defensive
        raise RuntimeError("upsert succeeded but row not found")
    # Mirror into storyland_entities so the entity registry stays
    # current. Sync is wrapped in try/except inside the helper — a
    # registry hiccup must never break the character save.
    from . import entity_registry as _er
    _er.sync_from_character(db_path, saved)
    return saved


def delete_character(db_path: str, character_id: str) -> None:
    with open_db(db_path) as con:
        con.execute("DELETE FROM characters WHERE character_id = ?",
                    (character_id,))
        con.commit()
    from . import entity_registry as _er
    _er.sync_delete_character(db_path, character_id)


# ─────────────── Worldbook entries ────────────────────────────────


def _worldbook_row_to_payload(row: sqlite3.Row,
                              include_embedding: bool = False) -> dict[str, Any]:
    r = _row_to_dict(row)
    out: dict[str, Any] = {
        "id": r["entry_id"],
        "project_id": r["project_id"],
        "title": r["title"],
        "category": r.get("category", "misc"),
        "content": r.get("content", ""),
        "tags": json.loads(r.get("tags_json") or "[]"),
        "sort_order": r.get("sort_order", 0),
        "created_at": r.get("created_at"),
        "updated_at": r.get("updated_at"),
    }
    if include_embedding:
        try:
            out["embedding"] = json.loads(r.get("embedding_json") or "[]")
        except (TypeError, json.JSONDecodeError):
            out["embedding"] = []
        out["embedding_text_hash"] = r.get("embedding_text_hash") or ""
        out["embedding_model_key"] = r.get("embedding_model_key") or ""
    return out


def worldbook_embedding_text(entry: dict[str, Any]) -> str:
    """Canonical text used to compute a worldbook entry's embedding.

    Title + category + content concatenated. Both the upsert path and
    the prompt-context loader's stale-check must derive the same string,
    so this helper is the single source of truth.
    """
    parts = [
        (entry.get("title") or "").strip(),
        (entry.get("category") or "").strip(),
        (entry.get("content") or "").strip(),
    ]
    return "\n".join(p for p in parts if p)


def _hash_embedding_text(text: str) -> str:
    import hashlib
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def list_worldbook(db_path: str, project_id: str | None = None,
                   include_embedding: bool = False) -> list[dict]:
    sql = (
        "SELECT * FROM worldbook_entries"
        + (" WHERE project_id = ?" if project_id else "")
        + " ORDER BY category, sort_order, title"
    )
    params = (project_id,) if project_id else ()
    with open_db(db_path) as con:
        rows = con.execute(sql, params).fetchall()
    return [_worldbook_row_to_payload(r, include_embedding=include_embedding) for r in rows]


def get_worldbook(db_path: str, entry_id: str,
                  include_embedding: bool = False) -> dict | None:
    with open_db(db_path) as con:
        row = con.execute(
            "SELECT * FROM worldbook_entries WHERE entry_id = ?",
            (entry_id,),
        ).fetchone()
    return _worldbook_row_to_payload(row, include_embedding=include_embedding) if row else None


def set_worldbook_embedding(
    db_path: str, entry_id: str,
    embedding: list[float], text_hash: str,
    model_key: str = "",
) -> None:
    """Persist a freshly-computed embedding for a worldbook entry.

    Used by the prompt_context loader to backfill embeddings lazily —
    upsert_worldbook intentionally clears the embedding (text-hash
    mismatch) so the loader recomputes against the current content.
    ``model_key`` is the active embedding model at compute time; the
    loader uses it on read to detect cross-model staleness after a
    user-driven model switch.
    """
    with open_db(db_path) as con:
        con.execute(
            "UPDATE worldbook_entries SET embedding_json = ?, "
            "embedding_text_hash = ?, embedding_model_key = ? "
            "WHERE entry_id = ?",
            (json.dumps(list(embedding), ensure_ascii=False),
             text_hash, model_key or "", entry_id),
        )
        con.commit()


_BANNED_WB_CATEGORIES = {
    "角色", "character", "characters", "主角", "配角", "反派",
}


def _coerce_worldbook_category(cat: str | None) -> str:
    """Reject character-shaped categories — those belong in `characters`.

    The DB CHECK constraint enforces the same; this is a friendlier
    surface-layer normalization that maps banned inputs to '杂项' with
    a debug log, so legacy clients don't break.

    See docs/MEMORY_VS_TRUTH.md for the worldbook-vs-characters rule.
    """
    raw = (cat or "杂项").strip()
    if raw.lower() in {c.lower() for c in _BANNED_WB_CATEGORIES}:
        import logging as _lg
        _lg.getLogger("inkoctobot.services.project_store").warning(
            "worldbook category=%r belongs in characters table — coerced to '杂项'",
            raw,
        )
        return "杂项"
    return raw


def upsert_worldbook(db_path: str, body: dict[str, Any]) -> dict[str, Any]:
    eid = body.get("id") or _nid("wb_")
    pid = body.get("project_id") or ""
    title = (body.get("title") or "").strip()
    if not title:
        raise ValueError("title is required")
    if not pid:
        raise ValueError("project_id is required")

    category = _coerce_worldbook_category(body.get("category"))
    content = body.get("content", "")
    new_text = worldbook_embedding_text(
        {"title": title, "category": category, "content": content}
    )
    new_hash = _hash_embedding_text(new_text)

    with open_db(db_path) as con:
        dup = con.execute(
            "SELECT entry_id FROM worldbook_entries "
            "WHERE project_id = ? AND title = ? AND entry_id != ?",
            (pid, title, eid),
        ).fetchone()
        if dup:
            raise ValueError(f"世界书条目「{title}」已存在，请使用不同的标题")

        # Preserve the existing embedding if the canonical text didn't
        # change — otherwise clear it so the loader recomputes lazily.
        prev_hash = ""
        prev_embed = "[]"
        prev = con.execute(
            "SELECT embedding_json, embedding_text_hash "
            "FROM worldbook_entries WHERE entry_id = ?",
            (eid,),
        ).fetchone()
        if prev is not None:
            prev_hash = prev["embedding_text_hash"] or ""
            prev_embed = prev["embedding_json"] or "[]"
        if prev_hash == new_hash:
            keep_embed_json, keep_hash = prev_embed, prev_hash
        else:
            keep_embed_json, keep_hash = "[]", ""

        con.execute(
            """INSERT INTO worldbook_entries (
                   entry_id, project_id, title, category, content,
                   tags_json, sort_order, embedding_json,
                   embedding_text_hash, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?,
                       CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
               ON CONFLICT(entry_id) DO UPDATE SET
                   project_id = excluded.project_id,
                   title = excluded.title,
                   category = excluded.category,
                   content = excluded.content,
                   tags_json = excluded.tags_json,
                   sort_order = excluded.sort_order,
                   embedding_json = excluded.embedding_json,
                   embedding_text_hash = excluded.embedding_text_hash,
                   updated_at = CURRENT_TIMESTAMP""",
            (eid, pid, title,
             category,
             content,
             json.dumps(body.get("tags", []), ensure_ascii=False),
             int(body.get("sort_order", 0) or 0),
             keep_embed_json,
             keep_hash),
        )
        con.commit()
    saved = get_worldbook(db_path, eid)
    if saved is None:  # pragma: no cover
        raise RuntimeError("upsert succeeded but row not found")
    # Mirror entry → storyland_entities iff category is stage-able
    # (地点/组织/物品). The helper itself decides + handles failures.
    from . import entity_registry as _er
    _er.sync_from_worldbook(db_path, saved)
    return saved


def delete_worldbook(db_path: str, entry_id: str) -> None:
    with open_db(db_path) as con:
        con.execute("DELETE FROM worldbook_entries WHERE entry_id = ?",
                    (entry_id,))
        con.commit()
    from . import entity_registry as _er
    _er.sync_delete_worldbook(db_path, entry_id)


# ─────────────── Project memory ───────────────────────────────────


def _memory_row_to_payload(row: sqlite3.Row) -> dict[str, Any]:
    r = _row_to_dict(row)
    return {
        "id": r["memory_id"],
        "content": r.get("content", ""),
        "category": r.get("category", "note"),
        "source": r.get("source", "user"),
        "created_at": r.get("created_at"),
        # legacy frontend uses "ts" — alias for compat
        "ts": r.get("created_at"),
    }


def get_project_memory(db_path: str, project_id: str) -> dict[str, Any]:
    with open_db(db_path) as con:
        rows = con.execute(
            "SELECT * FROM project_memories "
            "WHERE project_id = ? ORDER BY created_at",
            (project_id,),
        ).fetchall()
    return {
        "project_id": project_id,
        "memories": [_memory_row_to_payload(r) for r in rows],
    }


def add_project_memory(
    db_path: str, project_id: str, content: str,
    *, category: str = "note", source: str = "manual",
) -> dict[str, Any]:
    content = content.strip()
    if not content:
        raise ValueError("memory content is required")
    mid = _nid("mem_")
    with open_db(db_path) as con:
        con.execute(
            """INSERT INTO project_memories
               (memory_id, project_id, category, content, source, created_at)
               VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)""",
            (mid, project_id, category, content, source),
        )
        con.commit()
        row = con.execute(
            "SELECT * FROM project_memories WHERE memory_id = ?",
            (mid,),
        ).fetchone()
    return _memory_row_to_payload(row)


def replace_project_memory(
    db_path: str, project_id: str, memories: list[dict],
) -> None:
    """Replace all memory rows for a project. Used by the bulk PUT endpoint."""
    with open_db(db_path) as con:
        con.execute(
            "DELETE FROM project_memories WHERE project_id = ?",
            (project_id,),
        )
        for m in memories or []:
            if not isinstance(m, dict):
                continue
            content = (m.get("content") or "").strip()
            if not content:
                continue
            mid = m.get("id") or _nid("mem_")
            con.execute(
                """INSERT OR REPLACE INTO project_memories
                   (memory_id, project_id, category, content, source, created_at)
                   VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)""",
                (mid, project_id,
                 m.get("category", "note"),
                 content,
                 m.get("source", "user")),
            )
        con.commit()


def delete_project_memory_entry(
    db_path: str, project_id: str, memory_id: str,
) -> None:
    with open_db(db_path) as con:
        con.execute(
            "DELETE FROM project_memories "
            "WHERE memory_id = ? AND project_id = ?",
            (memory_id, project_id),
        )
        con.commit()


# ─────────────── helpers used by callers ──────────────────────────


def ensure_project_row(db_path: str, project_id: str,
                       title: str = "") -> None:
    """Insert a placeholder ``projects`` row if it doesn't exist.

    Useful when a new character / worldbook entry is added before the
    user has formally created a project (legacy code path).
    """
    if not project_id:
        return
    with open_db(db_path) as con:
        con.execute(
            """INSERT OR IGNORE INTO projects
               (project_id, title, status, created_at, updated_at)
               VALUES (?, ?, 'planning', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)""",
            (project_id, title),
        )
        con.commit()


# ─────────────── Storyline (nodes + edges) ────────────────────────


def get_storyline(db_path: str, project_id: str) -> dict[str, Any]:
    """Return the {nodes, edges} graph for one project."""
    with open_db(db_path) as con:
        nrows = con.execute(
            "SELECT * FROM storyline_nodes WHERE project_id = ? "
            "ORDER BY chapter_num NULLS LAST, node_id",
            (project_id,),
        ).fetchall()
        erows = con.execute(
            "SELECT * FROM storyline_edges WHERE project_id = ? ORDER BY edge_id",
            (project_id,),
        ).fetchall()
    nodes = []
    for r in nrows:
        rd = _row_to_dict(r)
        extra = json.loads(rd.get("extra_json") or "{}") or {}
        node = {
            "id": rd["node_id"],
            "title": rd.get("title") or "",
            "chapter_num": rd.get("chapter_num"),
            "summary": rd.get("summary") or "",
            "time": rd.get("time_label") or "",
            "location": rd.get("location") or "",
            "x": rd.get("pos_x") or 0,
            "y": rd.get("pos_y") or 0,
        }
        for k, v in extra.items():
            if k not in node:
                node[k] = v
        nodes.append(node)
    edges = [{
        "id": e["edge_id"],
        "from": e["from_node_id"],
        "to": e["to_node_id"],
        "label": e["label"] or "",
    } for e in erows]
    return {"project_id": project_id, "nodes": nodes, "edges": edges}


def replace_storyline(db_path: str, project_id: str,
                      nodes: list[dict], edges: list[dict]) -> None:
    """Wipe and re-insert the whole graph (matches the legacy PUT semantics)."""
    known_node_keys = {"id", "title", "chapter_num", "summary",
                       "time", "time_label", "location",
                       "x", "y", "pos_x", "pos_y"}
    with open_db(db_path) as con:
        # Edges first — they FK back to nodes.
        con.execute("DELETE FROM storyline_edges WHERE project_id = ?",
                    (project_id,))
        con.execute("DELETE FROM storyline_nodes WHERE project_id = ?",
                    (project_id,))
        for n in nodes or []:
            if not isinstance(n, dict):
                continue
            nid = n.get("id") or _nid("node_")
            extra = {k: v for k, v in n.items() if k not in known_node_keys}
            con.execute(
                """INSERT INTO storyline_nodes
                   (node_id, project_id, title, chapter_num, summary,
                    time_label, location, pos_x, pos_y, extra_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (nid, project_id,
                 n.get("title", ""),
                 n.get("chapter_num"),
                 n.get("summary", ""),
                 n.get("time") or n.get("time_label") or "",
                 n.get("location", ""),
                 float(n.get("x", n.get("pos_x", 0)) or 0),
                 float(n.get("y", n.get("pos_y", 0)) or 0),
                 json.dumps(extra, ensure_ascii=False)),
            )
        for e in edges or []:
            if not isinstance(e, dict):
                continue
            eid = e.get("id") or _nid("edge_")
            from_id = e.get("from") or e.get("from_node_id")
            to_id = e.get("to") or e.get("to_node_id")
            if not from_id or not to_id:
                continue
            con.execute(
                """INSERT INTO storyline_edges
                   (edge_id, project_id, from_node_id, to_node_id, label)
                   VALUES (?, ?, ?, ?, ?)""",
                (eid, project_id, from_id, to_id, e.get("label", "")),
            )
        con.commit()


# Writing knowledge helpers removed in v3.1. The writing_knowledge
# table is dropped by ``_drop_v1_redundant_objects`` in
# ``storage/project_schema.py``; CRUD endpoints and the prompt-context
# loader are gone.


# ─────────────── Chat history ─────────────────────────────────────


def get_chat_history(db_path: str, project_id: str,
                     scope: str = "pipeline") -> list[dict]:
    """Return ordered messages for one project/scope as the legacy shape."""
    with open_db(db_path) as con:
        rows = con.execute(
            "SELECT * FROM chat_messages "
            "WHERE project_id = ? AND scope = ? "
            "ORDER BY created_at, message_id",
            (project_id, scope),
        ).fetchall()
    out: list[dict] = []
    for r in rows:
        rd = _row_to_dict(r)
        meta = json.loads(rd.get("meta_json") or "{}") or {}
        msg = {
            "id": rd["message_id"],
            "role": rd["role"],
            "content": rd.get("content") or "",
            "ts": rd.get("created_at"),
        }
        for k, v in meta.items():
            if k not in msg:
                msg[k] = v
        out.append(msg)
    return out


def replace_chat_history(db_path: str, project_id: str, scope: str,
                         messages: list[dict]) -> None:
    """Replace all messages in one project/scope. Matches the PUT semantics.

    ``message_id`` is a global PRIMARY KEY but the DELETE only clears
    rows in this (project_id, scope) — the frontend often reuses the
    same client-side msg id across scopes (e.g. the same outline chat
    bubble feeds both ``outline_chat_<chapter>`` and ``pipeline_<chapter>``
    scopes during a generation). Using plain INSERT would then trip
    ``UNIQUE constraint failed: chat_messages.message_id`` on the
    second scope's PUT. ``INSERT OR REPLACE`` upserts cleanly: the
    scope's prior rows are already gone, and a cross-scope reuse just
    moves the row to the current scope, which is the intent."""
    with open_db(db_path) as con:
        con.execute(
            "DELETE FROM chat_messages WHERE project_id = ? AND scope = ?",
            (project_id, scope),
        )
        for idx, msg in enumerate(messages or []):
            if not isinstance(msg, dict):
                continue
            role = msg.get("role", "user")
            if role not in {"user", "assistant", "system", "tool"}:
                role = "user"
            mid = msg.get("id") or f"msg_{idx}_{_nid('')}"
            meta = {k: v for k, v in msg.items()
                    if k not in {"id", "role", "content", "ts"}}
            con.execute(
                """INSERT OR REPLACE INTO chat_messages
                   (message_id, project_id, scope, role, content,
                    meta_json, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)""",
                (mid, project_id, scope, role,
                 msg.get("content", ""),
                 json.dumps(meta, ensure_ascii=False)),
            )
        con.commit()


def clear_chat_history(db_path: str, project_id: str, scope: str) -> None:
    with open_db(db_path) as con:
        con.execute(
            "DELETE FROM chat_messages WHERE project_id = ? AND scope = ?",
            (project_id, scope),
        )
        con.commit()


# ─────────────── Editor document (volumes + chapters) ─────────────


def get_editor_doc(db_path: str, project_id: str) -> dict[str, Any]:
    """Return the full editor doc (volumes + chapters) for one project.

    Source of truth is ``project_blobs(scope='editor')``. Chapter content
    is also mirrored into the ``chapters`` table for direct SQL access
    by prompt-context loaders and the generation pipeline.
    """
    with open_db(db_path) as con:
        row = con.execute(
            "SELECT data_json FROM project_blobs "
            "WHERE project_id = ? AND scope = 'editor'",
            (project_id,),
        ).fetchone()
    if not row:
        return {"volumes": []}
    try:
        data = json.loads(row["data_json"] or "{}")
    except json.JSONDecodeError:
        data = {}
    if "volumes" not in data:
        data["volumes"] = []
    return data


def save_editor_doc(db_path: str, project_id: str,
                    doc: dict[str, Any]) -> dict[str, Any]:
    """Persist the editor doc. Writes two places atomically:

      1. ``project_blobs(scope='editor')`` — full UI-shaped blob.
      2. ``chapters`` rows — denormalized per-chapter columns so SQL
         queries (RAG, generation pipeline) can read directly.
    """
    body = dict(doc or {})
    body["saved_at"] = time.time()
    payload = json.dumps(body, ensure_ascii=False)

    with open_db(db_path) as con:
        # 1. Project row exists so all the FKs below resolve. Must run
        # FIRST — both project_blobs and chapters point at projects.
        con.execute(
            """INSERT OR IGNORE INTO projects
               (project_id, title, status, created_at, updated_at)
               VALUES (?, '', 'planning',
                       CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)""",
            (project_id,),
        )

        # 2. Source-of-truth blob (UPSERT).
        con.execute(
            """INSERT INTO project_blobs
               (blob_id, project_id, scope, data_json, updated_at)
               VALUES (?, ?, 'editor', ?, CURRENT_TIMESTAMP)
               ON CONFLICT(blob_id) DO UPDATE SET
                 data_json = excluded.data_json,
                 updated_at = CURRENT_TIMESTAMP""",
            (f"{project_id}__editor", project_id, payload),
        )

        # 3. Chapters denormalized. We REPLACE the project's chapter
        # rows so deletions in the UI are reflected; chapter_id is
        # stable per chapter_num so external refs remain valid.
        # NOTE: only the project's chapter_id namespace is cleared;
        # text_versions FK cascade from chapters_id is preserved.
        existing_ids = {
            r["chapter_id"]
            for r in con.execute(
                "SELECT chapter_id FROM chapters WHERE project_id = ?",
                (project_id,),
            )
        }
        kept_ids: set[str] = set()
        # Pending segment syncs — applied after the chapter commit so
        # segment_manager's own connection sees the latest chapter row.
        pending_segment_updates: list[tuple[str, str, str]] = []
        # chap_num must be UNIQUE per project (project_schema.py UNIQUE
        # constraint). Numbering it per-volume produced collisions across
        # multiple volumes ("v1 ch 1" + "v2 ch 1" both got chap_num=1 →
        # sqlite3.IntegrityError). Number globally, in document order.
        running_chap_num = 0
        for vol_idx, vol in enumerate(body.get("volumes", []) or []):
            if not isinstance(vol, dict):
                continue
            vol_num = int(vol.get("order", vol_idx) or 0) + 1
            for ch_idx, ch in enumerate(vol.get("chapters", []) or []):
                if not isinstance(ch, dict):
                    continue
                running_chap_num += 1
                chap_num = running_chap_num
                # Stable, project-scoped chapter_id. Prefer the id the
                # frontend has been tracking (`chapter_id` or `id`) so
                # text_versions FK + version history stay attached even
                # when the user reorders chapters. Fall back to a
                # generated id only for chapters that came in without one.
                cid = ch.get("chapter_id") or ch.get("id") \
                    or f"{project_id}_v{vol_num}_c{chap_num:04d}"
                kept_ids.add(cid)
                content = ch.get("content", "") or ""
                word_count = ch.get("word_count")
                if not isinstance(word_count, int):
                    word_count = len(content)
                # on_stage_entities: prefer the explicit blob if the
                # editor doc supplies one; otherwise derive from the
                # legacy ``characters`` list so the column is always
                # populated downstream of save_editor_doc.
                characters_list = ch.get("characters", []) or []
                ose = ch.get("on_stage_entities")
                if not isinstance(ose, dict):
                    ose = {}
                ose_characters = ose.get("characters") if isinstance(ose.get("characters"), list) else None
                on_stage_blob = {
                    "characters":    ose_characters if ose_characters is not None else characters_list,
                    "locations":     ose.get("locations") if isinstance(ose.get("locations"), list) else [],
                    "items":         ose.get("items") if isinstance(ose.get("items"), list) else [],
                    "organizations": ose.get("organizations") if isinstance(ose.get("organizations"), list) else [],
                }
                # Some prior versions of save_editor_doc numbered per-volume
                # AND fell back to a generated chapter_id even when the
                # frontend already had one — so the DB can carry a row at
                # (project_id, chap_num) whose chapter_id won't match the
                # one we're about to insert. The ``ON CONFLICT(chapter_id)``
                # clause won't catch that collision; the writer would 500
                # with UNIQUE(project_id, chapter_num). Free the slot
                # explicitly first so this save can land.
                stale = con.execute(
                    "SELECT chapter_id FROM chapters "
                    "WHERE project_id = ? AND chapter_num = ? AND chapter_id <> ?",
                    (project_id, chap_num, cid),
                ).fetchone()
                if stale is not None:
                    stale_id = stale["chapter_id"]
                    # Move the displaced chapter to a sentinel chap_num
                    # outside the valid range so its row isn't lost (the
                    # delete pass below will tidy up if it really vanished
                    # from the editor doc, otherwise a later iteration of
                    # this loop will re-claim its real chap_num).
                    con.execute(
                        "UPDATE chapters SET chapter_num = -ABS(chapter_num) "
                        "WHERE project_id = ? AND chapter_id = ?",
                        (project_id, stale_id),
                    )
                con.execute(
                    """INSERT INTO chapters (
                           chapter_id, project_id, chapter_num, volume,
                           title, outline, final_text, word_count,
                           synopsis, time_label, location, characters_json,
                           pov_character, on_stage_entities,
                           special_requirements,
                           status, scene_plan_json,
                           performance_log, evaluation_json,
                           extra_json, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                               'draft', '[]', '', '{}', '{}',
                               CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                       ON CONFLICT(chapter_id) DO UPDATE SET
                           chapter_num = excluded.chapter_num,
                           volume = excluded.volume,
                           title = excluded.title,
                           outline = excluded.outline,
                           final_text = excluded.final_text,
                           word_count = excluded.word_count,
                           synopsis = excluded.synopsis,
                           time_label = excluded.time_label,
                           location = excluded.location,
                           characters_json = excluded.characters_json,
                           pov_character = excluded.pov_character,
                           on_stage_entities = excluded.on_stage_entities,
                           special_requirements = excluded.special_requirements,
                           updated_at = CURRENT_TIMESTAMP""",
                    (cid, project_id, chap_num, vol_num,
                     ch.get("title", ""),
                     ch.get("outline", ""),
                     content,
                     word_count,
                     ch.get("synopsis", ""),
                     ch.get("time", ch.get("time_label", "")),
                     ch.get("location", ""),
                     json.dumps(characters_list, ensure_ascii=False),
                     ch.get("pov_character", ""),
                     json.dumps(on_stage_blob, ensure_ascii=False),
                     (ch.get("special_requirements") or "")),
                )
                pending_segment_updates.append((cid, project_id, content))

        # Delete rows that vanished from the editor doc (user removed
        # them in the UI). Don't touch chapter rows owned by other
        # writers (only those previously seeded by save_editor_doc).
        removed = existing_ids - kept_ids
        for cid in removed:
            con.execute(
                "DELETE FROM chapters WHERE chapter_id = ? AND project_id = ?",
                (cid, project_id),
            )
        # Any chapters we sentinelled to a negative chap_num to free a
        # slot, but didn't re-claim, are stale duplicates from the old
        # buggy numbering — drop them. ON DELETE CASCADE on text_versions
        # would only fire if a version was attached, which it won't be
        # since the user can't see those phantom rows.
        con.execute(
            "DELETE FROM chapters WHERE project_id = ? AND chapter_num < 0",
            (project_id,),
        )
        con.commit()

    # Sync chapter_segments after the chapter rows commit so the
    # segment manager's own connection sees the latest text. Failures
    # here must not abort the save — segments are a derived view.
    from . import segment_manager as _sm
    for cid, pid, content in pending_segment_updates:
        try:
            _sm.update_chapter_content(db_path, cid, pid, content)
        except Exception as e:  # pragma: no cover — defensive
            import logging as _lg
            _lg.getLogger("inkoctobot.services.project_store").warning(
                "segment sync failed for %s: %s", cid, e,
            )
    return {"saved_at": body["saved_at"]}


# ─────────────── Generic per-project blob (calibration, injection, ...) ──


def get_blob(db_path: str, project_id: str, scope: str,
             default: dict | None = None) -> dict:
    with open_db(db_path) as con:
        row = con.execute(
            "SELECT data_json FROM project_blobs "
            "WHERE project_id = ? AND scope = ?",
            (project_id, scope),
        ).fetchone()
    if not row:
        return dict(default) if default is not None else {}
    try:
        return json.loads(row["data_json"] or "{}")
    except json.JSONDecodeError:
        return dict(default) if default is not None else {}


def save_blob(db_path: str, project_id: str, scope: str,
              data: dict) -> None:
    payload = json.dumps(data, ensure_ascii=False)
    # FK requires the project row to exist.
    ensure_project_row(db_path, project_id)
    with open_db(db_path) as con:
        con.execute(
            """INSERT INTO project_blobs
               (blob_id, project_id, scope, data_json, updated_at)
               VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
               ON CONFLICT(blob_id) DO UPDATE SET
                 data_json = excluded.data_json,
                 updated_at = CURRENT_TIMESTAMP""",
            (f"{project_id}__{scope}", project_id, scope, payload),
        )
        con.commit()


def delete_blob(db_path: str, project_id: str, scope: str) -> None:
    with open_db(db_path) as con:
        con.execute(
            "DELETE FROM project_blobs "
            "WHERE project_id = ? AND scope = ?",
            (project_id, scope),
        )
        con.commit()


# ─────────────── Projects (root project metadata) ──────────────────


def _project_row_to_payload(row: sqlite3.Row) -> dict[str, Any]:
    r = _row_to_dict(row)
    extra = json.loads(r.get("style_profile_json") or "{}") or {}
    out = {
        "id": r["project_id"],
        "name": r.get("title") or "",
        "genre": r.get("genre") or "",
        "description": r.get("logline") or "",
        "status": r.get("status") or "planning",
        "current_chapter": r.get("current_chapter") or 0,
        "current_volume": r.get("current_volume") or 1,
        "world_book_path": r.get("world_book_path") or "",
        "model_preset": r.get("model_preset") or "balanced",
        "created_at": r.get("created_at"),
        "updated_at": r.get("updated_at"),
    }
    # Restore frontend-only keys stashed in style_profile_json.
    for k, v in extra.items():
        if k not in out:
            out[k] = v
    return out


def list_projects(db_path: str) -> list[dict]:
    with open_db(db_path) as con:
        rows = con.execute(
            "SELECT * FROM projects ORDER BY updated_at DESC, created_at DESC"
        ).fetchall()
    return [_project_row_to_payload(r) for r in rows]


def get_project(db_path: str, project_id: str) -> dict | None:
    with open_db(db_path) as con:
        row = con.execute(
            "SELECT * FROM projects WHERE project_id = ?",
            (project_id,),
        ).fetchone()
    return _project_row_to_payload(row) if row else None


def upsert_project(db_path: str, body: dict[str, Any]) -> dict[str, Any]:
    """Insert or update a project row from a frontend-shaped dict.

    Returns the resulting payload (in frontend shape).
    """
    pid = body.get("id") or _nid("proj_")
    known_cols = {
        "id", "name", "genre", "description", "status",
        "current_chapter", "current_volume", "world_book_path",
        "model_preset", "created_at", "updated_at",
    }
    extra = {k: v for k, v in body.items() if k not in known_cols}
    status = body.get("status", "planning")
    if status not in {"planning", "writing", "paused", "completed"}:
        status = "planning"

    with open_db(db_path) as con:
        con.execute(
            """INSERT INTO projects (
                   project_id, title, genre, logline, status,
                   current_chapter, current_volume, world_book_path,
                   style_profile_json, model_preset,
                   created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                       CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
               ON CONFLICT(project_id) DO UPDATE SET
                   title = excluded.title,
                   genre = excluded.genre,
                   logline = excluded.logline,
                   status = excluded.status,
                   current_chapter = excluded.current_chapter,
                   current_volume = excluded.current_volume,
                   world_book_path = excluded.world_book_path,
                   style_profile_json = excluded.style_profile_json,
                   model_preset = excluded.model_preset,
                   updated_at = CURRENT_TIMESTAMP""",
            (pid,
             body.get("name", ""),
             body.get("genre", ""),
             body.get("description", ""),
             status,
             int(body.get("current_chapter", 0) or 0),
             int(body.get("current_volume", 1) or 1),
             body.get("world_book_path", ""),
             json.dumps(extra, ensure_ascii=False),
             body.get("model_preset", "balanced")),
        )
        con.commit()
    saved = get_project(db_path, pid)
    if saved is None:  # pragma: no cover
        raise RuntimeError("upsert succeeded but row not found")
    return saved


def delete_project(db_path: str, project_id: str) -> None:
    """Delete a project + all CASCADE-linked rows (chapters, characters, ...)."""
    with open_db(db_path) as con:
        con.execute("DELETE FROM projects WHERE project_id = ?", (project_id,))
        con.commit()


# ─────────────── Text versions (chapter version chain) ────────────


def _version_row_to_payload(row: sqlite3.Row) -> dict[str, Any]:
    r = _row_to_dict(row)
    diff = json.loads(r.get("diff_json") or "{}") or {}
    # The frontend's TextVersion interface uses `version` / `text` /
    # `user_edited`-style source labels; the DB schema uses `version_num`
    # / `content` / `user_edit`. Surface BOTH spellings so the
    # version-history panel renders the saved text instead of an empty
    # box. The `source` mapping is the inverse of insert_version().
    raw_source = r.get("source") or "ai"
    source_display = {
        "ai":        "ai_generated",
        "user_edit": "user_edited",
        "rewrite":   "targeted_rewrite",
    }.get(raw_source, raw_source)
    # Diff bag may carry the original source label (e.g. auto_saved) that
    # didn't fit the DB CHECK; prefer it when present so the UI doesn't
    # mis-categorise auto-saves as user_edits.
    if isinstance(diff.get("source"), str):
        source_display = diff["source"]
    content = r.get("content") or ""
    out = {
        "version_id": r["version_id"],
        "chapter_id": r["chapter_id"],
        "version_num": r.get("version_num") or 1,
        "version":     r.get("version_num") or 1,  # frontend alias
        "source": source_display,
        "content": content,
        "text":    content,                          # frontend alias
        "model_used": r.get("model_used") or "",
        "created_at": r.get("created_at"),
        "ts": r.get("created_at"),
    }
    for k, v in diff.items():
        if k not in out:
            out[k] = v
    return out


def list_versions(db_path: str, project_id: str,
                  chapter_id: str = "") -> list[dict]:
    """List versions, optionally filtered by chapter_id.

    project_id is required for the path scope; we use chapter_id when
    given, otherwise list all versions for chapters that belong to
    project_id.
    """
    with open_db(db_path) as con:
        if chapter_id:
            rows = con.execute(
                "SELECT * FROM text_versions WHERE chapter_id = ? "
                "ORDER BY version_num DESC",
                (chapter_id,),
            ).fetchall()
        else:
            rows = con.execute(
                """SELECT tv.* FROM text_versions tv
                   JOIN chapters ch ON ch.chapter_id = tv.chapter_id
                   WHERE ch.project_id = ?
                   ORDER BY tv.created_at DESC""",
                (project_id,),
            ).fetchall()
    return [_version_row_to_payload(r) for r in rows]


_SOURCE_TO_DB = {
    # Frontend labels (TextVersion.source) → DB CHECK domain.
    "ai_generated":     "ai",
    "user_edited":      "user_edit",
    "targeted_rewrite": "rewrite",
    "outline_chat":     "ai",
    "auto_saved":       "user_edit",
    # DB labels — pass through.
    "ai":               "ai",
    "user_edit":        "user_edit",
    "rewrite":          "rewrite",
}


def insert_version(db_path: str, project_id: str,
                   version: dict[str, Any]) -> dict[str, Any]:
    """Append a new version to a chapter's history.

    Accepts both the legacy DB-shaped payload (``version_num`` / ``content``
    / ``ai`` / ``user_edit`` / ``rewrite``) and the frontend's TextVersion
    shape (``version`` / ``text`` / ``user_edited`` / ``auto_saved`` / …)
    so /api/data/versions saves carry the actual chapter text instead of
    landing as an empty `content` blob. Out-of-domain labels are mapped
    into the DB CHECK domain; the original is preserved in diff_json so
    the UI can still distinguish ``auto_saved`` from ``user_edited``.

    Requires ``chapter_id`` to exist in the chapters table (FK).
    Auto-increments ``version_num`` per chapter.
    """
    chapter_id = (version.get("chapter_id") or "").strip()
    if not chapter_id:
        raise ValueError("chapter_id is required")

    # Accept text/content interchangeably so the frontend's `text` field
    # (TextVersion interface) doesn't get dropped on insert.
    content = version.get("content")
    if not isinstance(content, str) or not content:
        content = version.get("text", "") or ""

    raw_source = (version.get("source") or "ai").strip()
    db_source = _SOURCE_TO_DB.get(raw_source, "ai")

    known_cols = {"version_id", "chapter_id", "version_num", "version",
                  "source", "content", "text", "model_used",
                  "created_at", "ts"}
    diff = {k: v for k, v in version.items() if k not in known_cols}
    # Preserve the original (frontend) source label so the next read
    # round-trip can render it.
    if raw_source and raw_source != db_source:
        diff.setdefault("source", raw_source)

    with open_db(db_path) as con:
        # Auto-increment per chapter
        max_num = con.execute(
            "SELECT COALESCE(MAX(version_num), 0) FROM text_versions "
            "WHERE chapter_id = ?",
            (chapter_id,),
        ).fetchone()[0]
        vnum = int(version.get("version_num") or version.get("version") or (max_num + 1))
        vid = version.get("version_id") or _nid("ver_")
        con.execute(
            """INSERT INTO text_versions
               (version_id, chapter_id, version_num, source, content,
                diff_json, model_used, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)""",
            (vid, chapter_id, vnum, db_source,
             content,
             json.dumps(diff, ensure_ascii=False),
             version.get("model_used", "")),
        )
        con.commit()
        row = con.execute(
            "SELECT * FROM text_versions WHERE version_id = ?",
            (vid,),
        ).fetchone()
    return _version_row_to_payload(row)


def delete_version(db_path: str, version_id: str) -> None:
    with open_db(db_path) as con:
        con.execute(
            "DELETE FROM text_versions WHERE version_id = ?",
            (version_id,),
        )
        con.commit()


# ─────────────── Foreshadowing (legacy compat) ────────────────────
#
# Truth Files (pending_hooks + hook_events) is the canonical store
# for foreshadowing. The legacy data/foreshadowing/<pid>.json file
# is gone in v2; the endpoint still serves a flat list for the
# 大纲 tab by reading from pending_hooks.


def list_foreshadowing_legacy(
    db_path: str, project_id: str, *, include_resolved: bool = False,
) -> list[dict]:
    """Return hooks shaped like the legacy {items: [...]} blob.

    By default returns ONLY active hooks — those the user has 已
    fully-resolved (user_marked_fully_resolved=1) are filtered out so
    the prompt loader can't re-surface them even if the auto-detector
    would otherwise re-open the same hook.

    ``include_resolved=True`` (used by the 故事线 page's 已回收 view)
    drops that filter so resolved hooks show in the inactive list.
    """
    where = "WHERE project_id = ?"
    if not include_resolved:
        where += " AND COALESCE(user_marked_fully_resolved, 0) = 0"
    with open_db(db_path) as con:
        try:
            rows = con.execute(
                "SELECT hook_id, description, origin_chapter, "
                "expected_payoff_chapter, status, importance, "
                "COALESCE(scale, 'event_clue') AS scale, "
                "COALESCE(user_marked_fully_resolved, 0) AS user_resolved "
                f"FROM pending_hooks {where} "
                "ORDER BY origin_chapter",
                (project_id,),
            ).fetchall()
        except sqlite3.OperationalError:
            return []
    out = []
    for r in rows:
        # If the user marked the hook fully resolved, the canonical status
        # client-side is "resolved" regardless of the auto-detector's
        # tracking field.
        status = "resolved" if r["user_resolved"] else r["status"]
        out.append({
            "id": r["hook_id"],
            "title": r["description"][:30],
            "content": r["description"],
            "chapter_ids": [],
            "origin_chapter": r["origin_chapter"],
            "expected_payoff_chapter": r["expected_payoff_chapter"],
            "status": status,
            "importance": r["importance"],
            "scale": r["scale"],
        })
    return out


def fully_resolve_hook(
    db_path: str, hook_id: str, *,
    chapter_num: int | None = None, notes: str = "",
) -> dict[str, Any]:
    """User-driven authoritative close — wins over any auto-detector update.

    Sets ``user_marked_fully_resolved=1`` plus optional bookkeeping
    fields, and flips ``status='resolved'`` so the hook is treated as
    closed by every reader. Idempotent: a second call on an already-
    resolved hook just refreshes the metadata.

    Returns the post-update hook row (or ``{}`` if no such hook exists).
    """
    with open_db(db_path) as con:
        cur = con.execute(
            "UPDATE pending_hooks "
            "SET user_marked_fully_resolved = 1, "
            "    user_resolved_at_chapter = ?, "
            "    user_resolve_notes = ?, "
            "    status = 'resolved' "
            "WHERE hook_id = ?",
            (chapter_num, notes or "", hook_id),
        )
        if cur.rowcount == 0:
            return {}
        con.commit()
        row = con.execute(
            "SELECT hook_id, project_id, description, status, "
            "       origin_chapter, importance, "
            "       user_marked_fully_resolved, user_resolved_at_chapter, "
            "       user_resolve_notes "
            "FROM pending_hooks WHERE hook_id = ?",
            (hook_id,),
        ).fetchone()
    return _row_to_dict(row) if row else {}


def reactivate_hook(db_path: str, hook_id: str) -> dict[str, Any]:
    """Undo a user fully-resolve: clear the resolve flag, blank the close
    chapter / notes, and recompute the status from earlier hook_events
    so 埋设 / 推进 history stays intact (only the 回收 event is dropped).

    Status rules:
      · If there is any earlier 'progress'/'mention' event → status
        becomes 'progressing'
      · Otherwise → 'open' (just 埋设)

    Returns the post-update hook row (or {} if no such hook exists).
    """
    with open_db(db_path) as con:
        # 1) Decide the next status by inspecting earlier events.
        rows = con.execute(
            "SELECT action FROM hook_events "
            "WHERE hook_id = ? AND action != 'resolve' "
            "ORDER BY chapter_num",
            (hook_id,),
        ).fetchall()
        had_progress = any(
            (r["action"] if hasattr(r, "keys") else r[0]) in (
                "progress", "mention", "pressured", "near_payoff",
            )
            for r in rows
        )
        next_status = "progressing" if had_progress else "open"

        # 2) Flip the hook row back.
        cur = con.execute(
            "UPDATE pending_hooks "
            "SET user_marked_fully_resolved = 0, "
            "    user_resolved_at_chapter = NULL, "
            "    user_resolve_notes = '', "
            "    status = ? "
            "WHERE hook_id = ?",
            (next_status, hook_id),
        )
        if cur.rowcount == 0:
            return {}

        # 3) Drop the matching 'resolve' hook_events so the 回收 章节 no
        #    longer carries that marker. 埋设/推进 events stay intact.
        con.execute(
            "DELETE FROM hook_events WHERE hook_id = ? AND action = 'resolve'",
            (hook_id,),
        )
        con.commit()

        row = con.execute(
            "SELECT hook_id, project_id, description, status, "
            "       origin_chapter, importance, "
            "       user_marked_fully_resolved, user_resolved_at_chapter "
            "FROM pending_hooks WHERE hook_id = ?",
            (hook_id,),
        ).fetchone()
    return _row_to_dict(row) if row else {}


# ─────────────── chapter_summaries anchor toggle ──────────────────


def toggle_chapter_summary_anchor(
    db_path: str, project_id: str, chapter_num: int,
) -> dict[str, Any] | None:
    """Flip ``is_anchor`` on the chapter_summaries row for this chapter.

    Returns the new state (``{"chapter_num", "is_anchor"}``), or None
    if no summary exists for the given (project_id, chapter_num) yet.
    Idempotent on the same value — anchoring twice still leaves it
    anchored after the second call.
    """
    with open_db(db_path) as con:
        row = con.execute(
            "SELECT is_anchor FROM chapter_summaries "
            "WHERE project_id = ? AND chapter_num = ?",
            (project_id, int(chapter_num)),
        ).fetchone()
        if row is None:
            return None
        new_state = 0 if row["is_anchor"] else 1
        con.execute(
            "UPDATE chapter_summaries SET is_anchor = ? "
            "WHERE project_id = ? AND chapter_num = ?",
            (new_state, project_id, int(chapter_num)),
        )
        con.commit()
    return {"chapter_num": int(chapter_num), "is_anchor": new_state}


def set_chapter_summary_anchor(
    db_path: str, project_id: str, chapter_num: int, is_anchor: bool,
) -> dict[str, Any] | None:
    """Explicit setter — like toggle but doesn't depend on the prior state."""
    with open_db(db_path) as con:
        row = con.execute(
            "SELECT 1 FROM chapter_summaries "
            "WHERE project_id = ? AND chapter_num = ?",
            (project_id, int(chapter_num)),
        ).fetchone()
        if row is None:
            return None
        con.execute(
            "UPDATE chapter_summaries SET is_anchor = ? "
            "WHERE project_id = ? AND chapter_num = ?",
            (1 if is_anchor else 0, project_id, int(chapter_num)),
        )
        con.commit()
    return {"chapter_num": int(chapter_num), "is_anchor": 1 if is_anchor else 0}


# ─────────────── B2: InkOS audit gate — chapter audit status ──────


_ALLOWED_AUDIT_STATUSES = {
    "audit_pending", "audit_passed", "audit_failed", "audit_overridden",
}


def update_chapter_audit(
    db_path: str, project_id: str, chapter_num: int,
    *, audit_status: str, audit_issues: list[dict[str, Any]],
) -> None:
    """Persist Phase 2 settlement audit result onto the chapter row.

    Called after extract_and_apply_state_deltas. The chapter is created
    via INSERT OR IGNORE if it doesn't exist yet (e.g. single-writer
    mode where the chapter row hasn't been provisioned). The audit
    fields are then UPDATEd unconditionally.
    """
    if audit_status not in _ALLOWED_AUDIT_STATUSES:
        audit_status = "audit_pending"
    chapter_id = f"{project_id}_ch{chapter_num:04d}"
    issues_json = json.dumps(audit_issues, ensure_ascii=False)
    with open_db(db_path) as con:
        con.execute(
            """INSERT OR IGNORE INTO chapters
               (chapter_id, project_id, chapter_num, status,
                audit_status, audit_issues_json,
                created_at, updated_at)
               VALUES (?, ?, ?, 'draft', ?, ?,
                       CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)""",
            (chapter_id, project_id, chapter_num,
             audit_status, issues_json),
        )
        con.execute(
            """UPDATE chapters
               SET audit_status = ?,
                   audit_issues_json = ?,
                   updated_at = CURRENT_TIMESTAMP
               WHERE project_id = ? AND chapter_num = ?""",
            (audit_status, issues_json, project_id, chapter_num),
        )
        con.commit()


def get_chapter_audit(
    db_path: str, project_id: str, chapter_num: int,
) -> dict[str, Any]:
    """Return the chapter's current audit status + parsed issues.

    Shape: ``{audit_status, audit_issues, exists}``. Empty defaults
    when the chapter row hasn't been touched yet.
    """
    with open_db(db_path) as con:
        try:
            row = con.execute(
                "SELECT audit_status, audit_issues_json FROM chapters "
                "WHERE project_id = ? AND chapter_num = ?",
                (project_id, chapter_num),
            ).fetchone()
        except sqlite3.OperationalError:
            # Old schema without audit columns — graceful default.
            return {"audit_status": "audit_pending", "audit_issues": [], "exists": False}
    if not row:
        return {"audit_status": "audit_pending", "audit_issues": [], "exists": False}
    try:
        issues = json.loads(row["audit_issues_json"] or "[]")
    except (TypeError, json.JSONDecodeError):
        issues = []
    return {
        "audit_status": row["audit_status"] or "audit_pending",
        "audit_issues": issues,
        "exists": True,
    }


def override_chapter_audit(
    db_path: str, project_id: str, chapter_num: int,
) -> None:
    """User explicitly overrides a failed audit — typically after they
    manually fix the underlying issue. Moves audit_failed → audit_overridden
    so the finalize-guard lets the chapter through.
    """
    with open_db(db_path) as con:
        con.execute(
            "UPDATE chapters SET audit_status='audit_overridden', "
            "updated_at=CURRENT_TIMESTAMP "
            "WHERE project_id=? AND chapter_num=? AND audit_status='audit_failed'",
            (project_id, chapter_num),
        )
        con.commit()


def can_finalize_chapter(
    db_path: str, project_id: str, chapter_num: int,
) -> tuple[bool, str]:
    """Hard gate: returns (allowed, reason). ``allowed=False`` if the
    chapter's audit_status is audit_failed and the user hasn't overridden.
    """
    info = get_chapter_audit(db_path, project_id, chapter_num)
    status = info["audit_status"]
    if status == "audit_failed":
        return False, (
            f"第 {chapter_num} 章 audit_failed — "
            "请在编辑器的「事实库审计」面板查看 issues 并修正，"
            "或显式 override 后再 finalize"
        )
    return True, ""
