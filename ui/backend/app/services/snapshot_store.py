"""Snapshot CRUD + bind / mark-complete helpers.

LOADER_SPEC Loader 5 (Batch 5) data layer. Pairs with
``character_snapshot_resolver`` (read-side decision logic) and
``snapshot_auto_detector`` / ``snapshot_reminder`` (write-side triggers
and bookkeeping).

The table is ordered per-character by ``snapshot_order`` (1-based).
Insert without ``snapshot_order`` appends to the end; explicit
``snapshot_order`` is honored if it doesn't collide with an existing
row.

``bound_chapters`` is the list of chapters where the transition
"happens" (an event). ``transition_complete_chapter`` is the chapter
at which the transition is considered complete; from then on the
snapshot is the baseline for that character.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import time
import uuid
from typing import Any

from .project_store import open_db, _row_to_dict

logger = logging.getLogger("inkoctobot.services.snapshot_store")


def _sid() -> str:
    return f"snap_{int(time.time() * 1000)}_{uuid.uuid4().hex[:6]}"


def _row_to_payload(row: sqlite3.Row) -> dict[str, Any]:
    r = _row_to_dict(row)
    return {
        "snapshot_id":                  r["snapshot_id"],
        "character_id":                 r["character_id"],
        "project_id":                   r["project_id"],
        "snapshot_order":               r["snapshot_order"],
        "expected_chapter_range_start": r.get("expected_chapter_range_start"),
        "expected_chapter_range_end":   r.get("expected_chapter_range_end"),
        "trigger_description":          r.get("trigger_description") or "",
        "personality_override":         r.get("personality_override") or "",
        "speech_style_override":        r.get("speech_style_override") or "",
        "alias":                        r.get("alias") or "",
        "layer_b_overrides":            json.loads(r.get("layer_b_overrides_json") or "{}"),
        "relations_overrides":          json.loads(r.get("relations_overrides_json") or "{}"),
        "other_changes":                r.get("other_changes") or "",
        "bound_chapters":               json.loads(r.get("bound_chapters_json") or "[]"),
        "transition_complete_chapter":  r.get("transition_complete_chapter"),
        # 角色卡·机制2: {"<章号>": "wavering|probing|leaning"}
        "transition_stages":            json.loads(r.get("transition_stages_json") or "{}"),
        "bound_by":                     r.get("bound_by") or "user",
        "bound_at":                     r.get("bound_at"),
        "created_at":                   r.get("created_at"),
        "updated_at":                   r.get("updated_at"),
    }


# ─────────── CRUD ───────────


def list_snapshots(db_path: str, character_id: str) -> list[dict]:
    with open_db(db_path) as con:
        rows = con.execute(
            "SELECT * FROM character_snapshots WHERE character_id = ? "
            "ORDER BY snapshot_order",
            (character_id,),
        ).fetchall()
    return [_row_to_payload(r) for r in rows]


def list_project_snapshots(db_path: str, project_id: str) -> list[dict]:
    """All snapshots for a project, used by the reminder scanner."""
    with open_db(db_path) as con:
        rows = con.execute(
            "SELECT * FROM character_snapshots WHERE project_id = ? "
            "ORDER BY character_id, snapshot_order",
            (project_id,),
        ).fetchall()
    return [_row_to_payload(r) for r in rows]


def get_snapshot(db_path: str, snapshot_id: str) -> dict | None:
    with open_db(db_path) as con:
        row = con.execute(
            "SELECT * FROM character_snapshots WHERE snapshot_id = ?",
            (snapshot_id,),
        ).fetchone()
    return _row_to_payload(row) if row else None


def upsert_snapshot(db_path: str, body: dict[str, Any]) -> dict[str, Any]:
    """Create a new snapshot or update an existing one.

    Required: ``character_id`` (and ``project_id`` on create). If
    ``snapshot_id`` is omitted, a new row is created; ``snapshot_order``
    is auto-assigned as ``max(existing) + 1`` when not provided.
    """
    sid = body.get("snapshot_id") or body.get("id")
    cid = body.get("character_id")
    pid = body.get("project_id")
    if not cid:
        raise ValueError("character_id is required")

    with open_db(db_path) as con:
        if sid:
            existing = con.execute(
                "SELECT * FROM character_snapshots WHERE snapshot_id = ?",
                (sid,),
            ).fetchone()
        else:
            existing = None
            sid = _sid()

        if existing is None and not pid:
            raise ValueError("project_id is required for new snapshots")

        # Resolve snapshot_order. New rows: caller-provided or auto-tail.
        if existing is None:
            order = body.get("snapshot_order")
            if order is None:
                row = con.execute(
                    "SELECT COALESCE(MAX(snapshot_order), 0) + 1 "
                    "FROM character_snapshots WHERE character_id = ?",
                    (cid,),
                ).fetchone()
                order = int(row[0] if row else 1)
            else:
                order = int(order)
        else:
            order = int(body.get("snapshot_order") or existing["snapshot_order"])

        # Detect UNIQUE collisions before INSERT.
        dup = con.execute(
            "SELECT snapshot_id FROM character_snapshots "
            "WHERE character_id = ? AND snapshot_order = ? AND snapshot_id != ?",
            (cid, order, sid),
        ).fetchone()
        if dup:
            raise ValueError(
                f"snapshot_order {order} already exists for character {cid}"
            )

        # 转变档位: omitted key on update keeps the stored value so an
        # older client PUT doesn't wipe user-set 档位 (角色卡·机制2).
        if "transition_stages" in body:
            stages = body.get("transition_stages") or {}
        elif existing is not None:
            try:
                stages = json.loads(existing["transition_stages_json"] or "{}")
            except Exception:
                stages = {}
        else:
            stages = {}

        params = (
            sid, cid, pid or (existing["project_id"] if existing else ""),
            order,
            body.get("expected_chapter_range_start"),
            body.get("expected_chapter_range_end"),
            body.get("trigger_description") or "",
            body.get("personality_override") or "",
            body.get("speech_style_override") or "",
            body.get("alias") or "",
            json.dumps(body.get("layer_b_overrides") or {}, ensure_ascii=False),
            json.dumps(body.get("relations_overrides") or {}, ensure_ascii=False),
            body.get("other_changes") or "",
            json.dumps(body.get("bound_chapters") or [], ensure_ascii=False),
            body.get("transition_complete_chapter"),
            json.dumps(stages, ensure_ascii=False),
            body.get("bound_by") or "user",
        )
        con.execute(
            """INSERT INTO character_snapshots (
                   snapshot_id, character_id, project_id, snapshot_order,
                   expected_chapter_range_start, expected_chapter_range_end,
                   trigger_description, personality_override,
                   speech_style_override, alias,
                   layer_b_overrides_json, relations_overrides_json,
                   other_changes, bound_chapters_json,
                   transition_complete_chapter, transition_stages_json,
                   bound_by, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                       CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
               ON CONFLICT(snapshot_id) DO UPDATE SET
                   character_id = excluded.character_id,
                   project_id = excluded.project_id,
                   snapshot_order = excluded.snapshot_order,
                   expected_chapter_range_start = excluded.expected_chapter_range_start,
                   expected_chapter_range_end = excluded.expected_chapter_range_end,
                   trigger_description = excluded.trigger_description,
                   personality_override = excluded.personality_override,
                   speech_style_override = excluded.speech_style_override,
                   alias = excluded.alias,
                   layer_b_overrides_json = excluded.layer_b_overrides_json,
                   relations_overrides_json = excluded.relations_overrides_json,
                   other_changes = excluded.other_changes,
                   bound_chapters_json = excluded.bound_chapters_json,
                   transition_complete_chapter = excluded.transition_complete_chapter,
                   transition_stages_json = excluded.transition_stages_json,
                   bound_by = excluded.bound_by,
                   updated_at = CURRENT_TIMESTAMP""",
            params,
        )
        con.commit()
    saved = get_snapshot(db_path, sid)
    if saved is None:  # pragma: no cover
        raise RuntimeError("upsert succeeded but row not found")
    return saved


def delete_snapshot(db_path: str, snapshot_id: str) -> None:
    with open_db(db_path) as con:
        con.execute(
            "DELETE FROM character_snapshots WHERE snapshot_id = ?",
            (snapshot_id,),
        )
        con.commit()


# ─────────── bind / mark-complete ───────────


def _mutate(db_path: str, snapshot_id: str, mutator) -> dict | None:
    """Helper: load → apply mutator(dict) → write back atomically."""
    with open_db(db_path) as con:
        row = con.execute(
            "SELECT * FROM character_snapshots WHERE snapshot_id = ?",
            (snapshot_id,),
        ).fetchone()
        if row is None:
            return None
        snap = _row_to_payload(row)
        mutator(snap)
        con.execute(
            """UPDATE character_snapshots SET
                   bound_chapters_json = ?,
                   transition_complete_chapter = ?,
                   bound_by = ?,
                   bound_at = CURRENT_TIMESTAMP,
                   updated_at = CURRENT_TIMESTAMP
               WHERE snapshot_id = ?""",
            (json.dumps(snap["bound_chapters"], ensure_ascii=False),
             snap.get("transition_complete_chapter"),
             snap.get("bound_by") or "user",
             snapshot_id),
        )
        con.commit()
    return get_snapshot(db_path, snapshot_id)


def bind_chapter(
    db_path: str, snapshot_id: str, chapter_num: int, *,
    source: str = "user",
) -> dict | None:
    """Add ``chapter_num`` to ``bound_chapters`` (idempotent + sorted).

    ``source='auto'`` lets the auto-detector bind without clobbering a
    user-managed snapshot; the helper preserves ``bound_by='user'``
    when applicable. ``source='user'`` always wins.
    """
    def mutate(snap: dict) -> None:
        prior_bound = list(snap.get("bound_chapters") or [])
        was_user_bound = (
            snap.get("bound_by") == "user" and bool(prior_bound)
        )
        bound = list(prior_bound)
        if int(chapter_num) not in bound:
            bound.append(int(chapter_num))
            bound.sort()
        snap["bound_chapters"] = bound
        if source == "user":
            snap["bound_by"] = "user"
        else:
            # 'auto' source — keep 'user' iff the snapshot was already
            # actively user-managed before this call.
            snap["bound_by"] = "user" if was_user_bound else "auto"
    return _mutate(db_path, snapshot_id, mutate)


def unbind_chapter(
    db_path: str, snapshot_id: str, chapter_num: int,
) -> dict | None:
    def mutate(snap: dict) -> None:
        snap["bound_chapters"] = [
            int(c) for c in (snap.get("bound_chapters") or [])
            if int(c) != int(chapter_num)
        ]
    return _mutate(db_path, snapshot_id, mutate)


def mark_complete(
    db_path: str, snapshot_id: str, chapter_num: int,
) -> dict | None:
    def mutate(snap: dict) -> None:
        snap["transition_complete_chapter"] = int(chapter_num)
        bound = list(snap.get("bound_chapters") or [])
        if int(chapter_num) not in bound:
            bound.append(int(chapter_num))
            bound.sort()
        snap["bound_chapters"] = bound
    return _mutate(db_path, snapshot_id, mutate)


def unmark_complete(db_path: str, snapshot_id: str) -> dict | None:
    def mutate(snap: dict) -> None:
        snap["transition_complete_chapter"] = None
    return _mutate(db_path, snapshot_id, mutate)


# ─────────── one-shot migration from extras_json.dynamic_snapshots ─────


def _parse_chapter(label: Any) -> int | None:
    """Pull the first integer out of a free-form chapter marker."""
    import re
    if label is None:
        return None
    m = re.search(r"\d+", str(label))
    return int(m.group(0)) if m else None


def migrate_dynamic_snapshots_extras(db_path: str) -> int:
    """One-shot move of ``characters.extra_json.dynamic_snapshots`` into
    the new ``character_snapshots`` table. Returns the number of
    snapshots imported. Safe to re-run — characters whose snapshots
    were already imported (matching order) are skipped.
    """
    n = 0
    with open_db(db_path) as con:
        char_rows = con.execute(
            "SELECT character_id, project_id, extra_json FROM characters"
        ).fetchall()
        for c in char_rows:
            try:
                extras = json.loads(c["extra_json"] or "{}") or {}
            except (TypeError, json.JSONDecodeError):
                continue
            snaps = extras.pop("dynamic_snapshots", None)
            if not isinstance(snaps, list) or not snaps:
                continue
            existing = con.execute(
                "SELECT snapshot_order FROM character_snapshots "
                "WHERE character_id = ?",
                (c["character_id"],),
            ).fetchall()
            existing_orders = {int(r["snapshot_order"]) for r in existing}
            for i, snap in enumerate(snaps, start=1):
                if not isinstance(snap, dict) or i in existing_orders:
                    continue
                ch_num = _parse_chapter(snap.get("chapter"))
                relations = {}
                # legacy hidden_identities stuffed into relations_overrides
                # under a reserved key so the loader can still surface them
                if isinstance(snap.get("hidden_identities"), list):
                    relations["__hidden_identities__"] = snap["hidden_identities"]
                if isinstance(snap.get("relationships"), list):
                    for rel in snap["relationships"]:
                        if isinstance(rel, dict) and rel.get("target_name"):
                            relations[rel["target_name"]] = {
                                "sentiment_score": rel.get("affinity"),
                                "trust_level":     rel.get("trust"),
                                "label":           rel.get("label") or "",
                                "notes":           rel.get("notes") or "",
                                "priority":        rel.get("priority"),
                            }
                con.execute(
                    """INSERT INTO character_snapshots (
                           snapshot_id, character_id, project_id,
                           snapshot_order,
                           expected_chapter_range_start,
                           trigger_description,
                           personality_override, speech_style_override,
                           layer_b_overrides_json, relations_overrides_json,
                           other_changes, bound_chapters_json,
                           bound_by)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'user')""",
                    (_sid(), c["character_id"], c["project_id"], i,
                     ch_num,
                     str(snap.get("notes") or "").strip(),
                     snap.get("personality") or "",
                     snap.get("speech_style") or "",
                     json.dumps(snap.get("layer_b") or {}, ensure_ascii=False),
                     json.dumps(relations, ensure_ascii=False),
                     snap.get("background") or "",
                     json.dumps([ch_num] if ch_num is not None else [],
                                ensure_ascii=False)),
                )
                n += 1
            # Cleared extras_json key (so re-reading the character row
            # doesn't expose the legacy field).
            con.execute(
                "UPDATE characters SET extra_json = ?, updated_at = CURRENT_TIMESTAMP "
                "WHERE character_id = ?",
                (json.dumps(extras, ensure_ascii=False), c["character_id"]),
            )
        con.commit()
    logger.info("migrate_dynamic_snapshots_extras imported %d snapshots", n)
    return n
