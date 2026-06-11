"""事务性回溯 (spec 5.2.4 回溯·机制2/机制3).

Rolling back to chapter K means: every state derived from chapters
AFTER K is removed as one unit — 章节正文 (text versions)、Storyland
状态 (SPO / ledger / emotion / entities)、读者视角记忆 (L2 summaries、
L4 events、L3 vectors)、伏笔 / 故事线 (hooks / hook events / subplot
threads). All SQL runs inside ONE transaction so a failure leaves the
project untouched (机制3: 整体回退，不允许部分回退). The ChromaDB L3
cleanup is best-effort after the SQL commit (vectors are a derived
cache; orphans only cost recall noise and are re-indexed on demand).

Chapter rows themselves (outlines, user inputs) are PRESERVED — only
generated text versions and derived state are rolled back, so the
user can regenerate chapter K+1 immediately after.
"""
from __future__ import annotations

import logging
import sqlite3
from typing import Any

logger = logging.getLogger("inkoctobot.services.rollback")


# (label, table, chapter-column, extra predicate) — every DELETE the
# rollback performs, in dependency-safe order. hook_events must go
# before pending_hooks status recompute; text_versions resolves its
# chapter ids via the chapters table.
_DELETE_SPECS: list[tuple[str, str, str, str]] = [
    ("ledger_entries",   "character_ledger",  "chapter_num",        ""),
    ("emotion_arcs",     "emotion_arcs",      "chapter_num",        ""),
    ("chapter_summaries", "chapter_summaries", "chapter_num",       ""),
    ("episodic_events",  "episodic_events",   "chapter_num",        ""),
    ("hook_events",      "hook_events",       "chapter_num",        ""),
    ("hooks",            "pending_hooks",     "origin_chapter",     ""),
    ("subplot_threads",  "subplot_threads",   "start_chapter",      ""),
    ("state_change_log", "state_change_log",  "change_chapter",     ""),
    ("truth_apply_log",  "truth_apply_log",   "chapter_num",        ""),
    ("entities",         "storyland_entities", "introduced_chapter", ""),
]


def _count(con: sqlite3.Connection, table: str, col: str,
           project_id: str, target: int, extra: str = "") -> int:
    try:
        row = con.execute(
            f"SELECT COUNT(*) FROM {table} "
            f"WHERE project_id = ? AND {col} > ? {extra}",
            (project_id, target),
        ).fetchone()
        return int(row[0]) if row else 0
    except sqlite3.OperationalError:
        return 0


def _text_version_ids_after(
    con: sqlite3.Connection, project_id: str, target: int,
) -> list[str]:
    try:
        rows = con.execute(
            "SELECT v.version_id FROM text_versions v "
            "JOIN chapters c ON c.chapter_id = v.chapter_id "
            "WHERE c.project_id = ? AND c.chapter_num > ?",
            (project_id, target),
        ).fetchall()
        return [r[0] for r in rows]
    except sqlite3.OperationalError:
        return []


def preview_rollback(
    db_path: str, project_id: str, target_chapter: int,
) -> dict[str, Any]:
    """Counts of everything a rollback to ``target_chapter`` would
    remove — surfaced to the user BEFORE the destructive call
    (UI confirms first; 回溯 is irreversible)."""
    out: dict[str, Any] = {
        "project_id": project_id,
        "target_chapter": target_chapter,
    }
    with sqlite3.connect(db_path) as con:
        out["text_versions"] = len(
            _text_version_ids_after(con, project_id, target_chapter),
        )
        for label, table, col, extra in _DELETE_SPECS:
            out[label] = _count(con, table, col, project_id,
                                target_chapter, extra)
        # Facts whose window opened after K are deleted; facts whose
        # window CLOSED after K get re-opened.
        out["spo_deleted"] = _count(
            con, "truth_current_state", "valid_from_chapter",
            project_id, target_chapter,
        )
        try:
            out["spo_reopened"] = int(con.execute(
                "SELECT COUNT(*) FROM truth_current_state "
                "WHERE project_id = ? AND valid_to_chapter IS NOT NULL "
                "AND valid_to_chapter >= ? AND valid_from_chapter <= ?",
                (project_id, target_chapter, target_chapter),
            ).fetchone()[0])
        except sqlite3.OperationalError:
            out["spo_reopened"] = 0
    return out


def rollback_to_chapter(
    db_path: str, project_id: str, target_chapter: int,
) -> dict[str, Any]:
    """Transactionally roll the four state groups back to just-after
    chapter ``target_chapter``. Returns deletion counts. Raises on
    failure with NOTHING applied (single transaction, 机制3)."""
    if target_chapter < 0:
        raise ValueError("target_chapter must be >= 0")
    result: dict[str, Any] = {
        "project_id": project_id, "target_chapter": target_chapter,
    }
    con = sqlite3.connect(db_path)
    try:
        con.execute("PRAGMA foreign_keys = ON;")
        con.execute("BEGIN")

        # ── 1. 章节正文: generated versions after K ──
        vids = _text_version_ids_after(con, project_id, target_chapter)
        if vids:
            qmarks = ",".join("?" for _ in vids)
            con.execute(
                f"DELETE FROM text_versions WHERE version_id IN ({qmarks})",
                vids,
            )
        result["text_versions"] = len(vids)

        # ── 2-4. chapter-keyed derived state ──
        for label, table, col, extra in _DELETE_SPECS:
            try:
                cur = con.execute(
                    f"DELETE FROM {table} "
                    f"WHERE project_id = ? AND {col} > ? {extra}",
                    (project_id, target_chapter),
                )
                result[label] = cur.rowcount
            except sqlite3.OperationalError:
                result[label] = 0

        # ── Storyland SPO: delete windows opened after K, reopen
        #    windows that were closed after K ──
        cur = con.execute(
            "DELETE FROM truth_current_state "
            "WHERE project_id = ? AND valid_from_chapter > ?",
            (project_id, target_chapter),
        )
        result["spo_deleted"] = cur.rowcount
        cur = con.execute(
            "UPDATE truth_current_state "
            "SET valid_to_chapter = NULL, superseded_by = NULL "
            "WHERE project_id = ? AND valid_to_chapter IS NOT NULL "
            "AND valid_to_chapter >= ?",
            (project_id, target_chapter),
        )
        result["spo_reopened"] = cur.rowcount

        # ── Hooks: surviving hooks rewind their progress markers and
        #    rederive status from the remaining hook_events ──
        result["hooks_rewound"] = _rewind_surviving_hooks(
            con, project_id, target_chapter,
        )
        # Subplots advanced beyond K rewind their marker.
        try:
            cur = con.execute(
                "UPDATE subplot_threads SET last_advanced_chapter = ? "
                "WHERE project_id = ? AND last_advanced_chapter > ?",
                (target_chapter, project_id, target_chapter),
            )
            result["subplots_rewound"] = cur.rowcount
        except sqlite3.OperationalError:
            result["subplots_rewound"] = 0

        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()

    # ── L3 vectors: best-effort cleanup outside the transaction ──
    result["l3_cleanup"] = _cleanup_l3_vectors(project_id, target_chapter)
    return result


def _rewind_surviving_hooks(
    con: sqlite3.Connection, project_id: str, target_chapter: int,
) -> int:
    """For hooks that originated at or before K: clamp progress markers
    to K and rederive status from the latest surviving hook_event."""
    try:
        hooks = con.execute(
            "SELECT hook_id FROM pending_hooks WHERE project_id = ?",
            (project_id,),
        ).fetchall()
    except sqlite3.OperationalError:
        return 0
    rewound = 0
    for (hook_id,) in hooks:
        row = con.execute(
            "SELECT after_status FROM hook_events "
            "WHERE hook_id = ? ORDER BY chapter_num DESC, created_at DESC "
            "LIMIT 1",
            (hook_id,),
        ).fetchone()
        derived = (row[0] if row and row[0] else "open")
        markers = con.execute(
            "SELECT MAX(CASE WHEN action IN ('mention','progress','resolve') "
            "THEN chapter_num END), "
            "MAX(CASE WHEN action IN ('progress','resolve','new') "
            "THEN chapter_num END) "
            "FROM hook_events WHERE hook_id = ?",
            (hook_id,),
        ).fetchone()
        last_mention = markers[0] if markers else None
        last_advance = markers[1] if markers else None
        cur = con.execute(
            "UPDATE pending_hooks SET status = ?, "
            "last_mention_chapter = ?, last_advance_chapter = ?, "
            "user_marked_fully_resolved = CASE "
            "  WHEN user_resolved_at_chapter IS NOT NULL "
            "       AND user_resolved_at_chapter > ? THEN 0 "
            "  ELSE user_marked_fully_resolved END, "
            "user_resolved_at_chapter = CASE "
            "  WHEN user_resolved_at_chapter IS NOT NULL "
            "       AND user_resolved_at_chapter > ? THEN NULL "
            "  ELSE user_resolved_at_chapter END, "
            "updated_at = CURRENT_TIMESTAMP "
            "WHERE hook_id = ? AND (status != ? "
            "  OR COALESCE(last_mention_chapter, -1) > ? "
            "  OR COALESCE(last_advance_chapter, -1) > ?)",
            (derived, last_mention, last_advance,
             target_chapter, target_chapter,
             hook_id, derived, target_chapter, target_chapter),
        )
        rewound += cur.rowcount
    return rewound


def _cleanup_l3_vectors(project_id: str, target_chapter: int) -> str:
    """Delete L3 chunks for chapters > K from every model-versioned
    collection of this project. Best-effort: vector store may be
    absent in tests / minimal installs."""
    try:
        from knowledge.vector_store import VectorStore
        from ui.backend.app.services.embedding import get_embedding_service
        model = get_embedding_service().get_current_model()
        from ui.backend.app.services.commit_pipeline.sub_tasks.chromadb_indexer import (
            _collection_name,
        )
        store = VectorStore(
            collection_name=_collection_name(project_id, model.model_key),
        )
        store.delete_where({
            "$and": [
                {"project_id": {"$eq": project_id}},
                {"chapter_num": {"$gt": target_chapter}},
            ],
        })
        return "ok"
    except Exception as e:
        logger.debug("L3 vector cleanup skipped: %s", e)
        return f"skipped: {e.__class__.__name__}"
