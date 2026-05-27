"""Storyland state loader (LOADER_SPEC Loader 10).

Renders the small, queryable view of the storyland that the Writer
needs to ground every chapter: per-entity SPO triples, the character
particle ledger, and recent emotion arcs.

Filtered to the chapter's on-stage entities (passed in via
``on_stage_entities``), so a chapter that only mentions characters
doesn't show a wall of location / item state.

Relations matrix was intentionally moved out in Batch 1 — pairwise
relationships now live on ``character_snapshots.relations_overrides``
and surface via the character_cards loader.
"""
from __future__ import annotations

import logging
import sqlite3
from typing import Any

from ..budgets import BUDGETS
from ..utils import clip, section

logger = logging.getLogger("inkoctobot.services.prompt_context.storyland_state")


_ENTITY_KEYS_TO_SUBJECT_TYPE = [
    ("characters",    "character",    "角色当前位置 / 状态"),
    ("locations",     "location",     "地点状态"),
    ("items",         "item",         "关键物品状态"),
    ("organizations", "organization", "组织状态"),
]


def _query_state_for_subjects(
    db_path: str, project_id: str, subject_type: str,
    subjects: list[str], chapter_num: int,
) -> list[dict]:
    if not subjects:
        return []
    placeholders = ",".join("?" for _ in subjects)
    sql = (
        "SELECT subject, predicate, object, valid_from_chapter "
        "FROM truth_current_state "
        "WHERE project_id = ? AND subject_type = ? "
        "AND valid_from_chapter < ? "
        "AND (valid_to_chapter IS NULL OR valid_to_chapter >= ?) "
        f"AND subject IN ({placeholders}) "
        "ORDER BY subject, valid_from_chapter DESC"
    )
    params = [project_id, subject_type, chapter_num, chapter_num] + list(subjects)
    with sqlite3.connect(db_path) as con:
        con.row_factory = sqlite3.Row
        try:
            rows = con.execute(sql, params).fetchall()
        except sqlite3.OperationalError:
            return []
    return [dict(r) for r in rows]


def _query_ledger(
    db_path: str, project_id: str, characters: list[str], chapter_num: int,
) -> list[dict]:
    if not characters:
        return []
    placeholders = ",".join("?" for _ in characters)
    sql = (
        "SELECT character_name, key, new_value, chapter_num, delta "
        "FROM character_ledger "
        "WHERE project_id = ? "
        f"AND character_name IN ({placeholders}) "
        "AND chapter_num < ? "
        "ORDER BY character_name, key, chapter_num DESC"
    )
    params = [project_id] + list(characters) + [chapter_num]
    with sqlite3.connect(db_path) as con:
        con.row_factory = sqlite3.Row
        try:
            rows = con.execute(sql, params).fetchall()
        except sqlite3.OperationalError:
            return []
    # Keep only the latest row per (character, key).
    latest: dict[tuple[str, str], dict] = {}
    for r in rows:
        key = (r["character_name"], r["key"])
        if key not in latest:
            latest[key] = dict(r)
    return list(latest.values())


def _query_emotion_arcs(
    db_path: str, project_id: str, characters: list[str],
    chapter_num: int, window: int = 3,
) -> list[dict]:
    if not characters:
        return []
    placeholders = ",".join("?" for _ in characters)
    sql = (
        "SELECT character_name, from_state, to_state, trigger, chapter_num "
        "FROM emotion_arcs "
        "WHERE project_id = ? "
        f"AND character_name IN ({placeholders}) "
        "AND chapter_num < ? "
        "AND chapter_num >= ? "
        "ORDER BY character_name, chapter_num DESC"
    )
    params = [project_id] + list(characters) + [
        chapter_num, max(0, chapter_num - window),
    ]
    with sqlite3.connect(db_path) as con:
        con.row_factory = sqlite3.Row
        try:
            rows = con.execute(sql, params).fetchall()
        except sqlite3.OperationalError:
            return []
    return [dict(r) for r in rows]


def load(
    project_id: str,
    chapter_num: int,
    on_stage_entities: dict[str, list[str]] | None = None,
    *,
    pov_character: str | None = None,
    exclude: set | None = None,
) -> str:
    """Build the storyland-state block for the chapter."""
    on_stage_entities = on_stage_entities or {}
    try:
        from ui.backend.app.services.project_paths import get_db_path
        db_path = get_db_path()

        parts: list[str] = []

        for env_key, subject_type, header in _ENTITY_KEYS_TO_SUBJECT_TYPE:
            entities = list(on_stage_entities.get(env_key) or [])
            if exclude:
                entities = [e for e in entities if e not in exclude]
            if not entities:
                continue
            rows = _query_state_for_subjects(
                db_path, project_id, subject_type, entities, chapter_num,
            )
            if not rows:
                continue
            parts.append(f"### {header}")
            for r in rows:
                parts.append(
                    f"- {r['subject']} {r['predicate']} {r['object']}"
                    f"（自第 {r['valid_from_chapter']} 章）"
                )

        characters = list(on_stage_entities.get("characters") or [])
        ledger = _query_ledger(db_path, project_id, characters, chapter_num)
        if ledger:
            if parts:
                parts.append("")
            parts.append("### 角色资源 ledger")
            parts.append("| 角色 | 项目 | 当前值 | 最后变更 |")
            parts.append("|---|---|---|---|")
            for l in ledger:
                delta = l.get("delta") or 0
                delta_str = f"+{delta}" if delta > 0 else str(delta)
                parts.append(
                    f"| {l['character_name']} | {l['key']} | "
                    f"{l['new_value']} | 第 {l['chapter_num']} 章 {delta_str} |"
                )

        arcs = _query_emotion_arcs(db_path, project_id, characters, chapter_num)
        if arcs:
            if parts:
                parts.append("")
            parts.append("### 情绪轨迹（近 3 章）")
            for a in arcs:
                parts.append(
                    f"- {a['character_name']}: {a['from_state']} -> {a['to_state']}"
                    f"（自第 {a['chapter_num']} 章，触发: {a.get('trigger') or '—'}）"
                )

        if not parts:
            return ""
        title = f"Storyland 客观状态（截至第 {chapter_num - 1} 章）"
        body = clip("\n".join(parts), BUDGETS["storyland_state"])
        return section(title, body)
    except Exception as e:
        logger.debug("storyland_state skipped: %s", e)
        return ""
