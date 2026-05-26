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
    return saved


def delete_character(db_path: str, character_id: str) -> None:
    with open_db(db_path) as con:
        con.execute("DELETE FROM characters WHERE character_id = ?",
                    (character_id,))
        con.commit()


# ─────────────── Worldbook entries ────────────────────────────────


def _worldbook_row_to_payload(row: sqlite3.Row) -> dict[str, Any]:
    r = _row_to_dict(row)
    return {
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


def list_worldbook(db_path: str, project_id: str | None = None) -> list[dict]:
    sql = (
        "SELECT * FROM worldbook_entries"
        + (" WHERE project_id = ?" if project_id else "")
        + " ORDER BY category, sort_order, title"
    )
    params = (project_id,) if project_id else ()
    with open_db(db_path) as con:
        rows = con.execute(sql, params).fetchall()
    return [_worldbook_row_to_payload(r) for r in rows]


def get_worldbook(db_path: str, entry_id: str) -> dict | None:
    with open_db(db_path) as con:
        row = con.execute(
            "SELECT * FROM worldbook_entries WHERE entry_id = ?",
            (entry_id,),
        ).fetchone()
    return _worldbook_row_to_payload(row) if row else None


def upsert_worldbook(db_path: str, body: dict[str, Any]) -> dict[str, Any]:
    eid = body.get("id") or _nid("wb_")
    pid = body.get("project_id") or ""
    title = (body.get("title") or "").strip()
    if not title:
        raise ValueError("title is required")
    if not pid:
        raise ValueError("project_id is required")

    with open_db(db_path) as con:
        dup = con.execute(
            "SELECT entry_id FROM worldbook_entries "
            "WHERE project_id = ? AND title = ? AND entry_id != ?",
            (pid, title, eid),
        ).fetchone()
        if dup:
            raise ValueError(f"世界书条目「{title}」已存在，请使用不同的标题")

        con.execute(
            """INSERT INTO worldbook_entries (
                   entry_id, project_id, title, category, content,
                   tags_json, sort_order, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?,
                       CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
               ON CONFLICT(entry_id) DO UPDATE SET
                   project_id = excluded.project_id,
                   title = excluded.title,
                   category = excluded.category,
                   content = excluded.content,
                   tags_json = excluded.tags_json,
                   sort_order = excluded.sort_order,
                   updated_at = CURRENT_TIMESTAMP""",
            (eid, pid, title,
             body.get("category", "misc"),
             body.get("content", ""),
             json.dumps(body.get("tags", []), ensure_ascii=False),
             int(body.get("sort_order", 0) or 0)),
        )
        con.commit()
    saved = get_worldbook(db_path, eid)
    if saved is None:  # pragma: no cover
        raise RuntimeError("upsert succeeded but row not found")
    return saved


def delete_worldbook(db_path: str, entry_id: str) -> None:
    with open_db(db_path) as con:
        con.execute("DELETE FROM worldbook_entries WHERE entry_id = ?",
                    (entry_id,))
        con.commit()


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


# ─────────────── Writing knowledge (cross-project) ────────────────


def _knowledge_row_to_payload(row: sqlite3.Row) -> dict[str, Any]:
    r = _row_to_dict(row)
    return {
        "id": r["knowledge_id"],
        "domain": r.get("domain") or "general",
        "title": r.get("title") or "",
        "content": r.get("content") or "",
        "tags": json.loads(r.get("tags_json") or "[]"),
        "created_at": r.get("created_at"),
        "updated_at": r.get("updated_at"),
    }


def list_writing_knowledge(db_path: str,
                           domain: str | None = None) -> list[dict]:
    sql = "SELECT * FROM writing_knowledge"
    params: tuple = ()
    if domain:
        sql += " WHERE domain = ?"
        params = (domain,)
    sql += " ORDER BY domain, title"
    with open_db(db_path) as con:
        rows = con.execute(sql, params).fetchall()
    return [_knowledge_row_to_payload(r) for r in rows]


def get_writing_knowledge(db_path: str, knowledge_id: str) -> dict | None:
    with open_db(db_path) as con:
        row = con.execute(
            "SELECT * FROM writing_knowledge WHERE knowledge_id = ?",
            (knowledge_id,),
        ).fetchone()
    return _knowledge_row_to_payload(row) if row else None


def upsert_writing_knowledge(db_path: str,
                             body: dict[str, Any]) -> dict[str, Any]:
    kid = body.get("id") or _nid("wk_")
    title = (body.get("title") or "").strip()
    if not title:
        raise ValueError("title is required")
    with open_db(db_path) as con:
        dup = con.execute(
            "SELECT knowledge_id FROM writing_knowledge "
            "WHERE title = ? AND knowledge_id != ?",
            (title, kid),
        ).fetchone()
        if dup:
            raise ValueError(f"写作知识「{title}」已存在，请使用不同的标题")
        con.execute(
            """INSERT INTO writing_knowledge
               (knowledge_id, domain, title, content, tags_json,
                created_at, updated_at)
               VALUES (?, ?, ?, ?, ?,
                       CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
               ON CONFLICT(knowledge_id) DO UPDATE SET
                   domain = excluded.domain,
                   title = excluded.title,
                   content = excluded.content,
                   tags_json = excluded.tags_json,
                   updated_at = CURRENT_TIMESTAMP""",
            (kid,
             body.get("domain", "general"),
             title,
             body.get("content", ""),
             json.dumps(body.get("tags", []), ensure_ascii=False)),
        )
        con.commit()
    saved = get_writing_knowledge(db_path, kid)
    if saved is None:  # pragma: no cover
        raise RuntimeError("upsert succeeded but row not found")
    return saved


def delete_writing_knowledge(db_path: str, knowledge_id: str) -> None:
    with open_db(db_path) as con:
        con.execute(
            "DELETE FROM writing_knowledge WHERE knowledge_id = ?",
            (knowledge_id,),
        )
        con.commit()


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
    """Replace all messages in one project/scope. Matches the PUT semantics."""
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
                """INSERT INTO chat_messages
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
