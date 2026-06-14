"""Post-commit state review + manual fallback API (spec § 模块 5/6).

After the state_extractor sub-task auto-writes deltas into the
canonical tables, the user opens a review dialog to inspect /
correct / cancel each change. Every correction is a NEW row in
``state_change_log`` (append-only audit) plus a mirror write to the
canonical table to keep loaders consistent.

Read endpoints (4 dialog tabs):
- GET /api/state-review/{chapter_id}/state    — Tab 1 SPO changes
- GET /api/state-review/{chapter_id}/ledger   — Tab 2 ledger changes
- GET /api/state-review/{chapter_id}/emotion  — Tab 3 emotion arcs
- GET /api/state-review/{chapter_id}/entities — Tab 4 entity review queue

Write endpoints (correction CRUD):
- POST /api/state-changes  (manual create / correct SPO)
- PUT  /api/state-changes/{log_id}  (edit + log correction row)
- POST /api/state-changes/{log_id}/cancel  (cancellation row)
- POST /api/ledger-changes  (manual create)
- DELETE /api/ledger-changes/{ledger_id}  (append reverse delta)
- POST /api/emotion-arcs  (manual create)
- DELETE /api/emotion-arcs/{arc_id}  (append cancellation)
- POST /api/entities  (manual create)
- POST /api/entities/merge  (collapse two entities)
- POST /api/entity-review-queue/{queue_id}/decision  (merge / create / discard)
"""
from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Body, HTTPException

from ..services.project_paths import get_db_path

logger = logging.getLogger("inkoctobot.routers.state_review_api")


router = APIRouter(prefix="/api", tags=["state-review"])


def _now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds")


def _new_log_id() -> str:
    return f"sl_{uuid.uuid4().hex[:12]}"


# ─────────── 确认 gate: pending state-extraction proposals ───────────
# spec Post-Commit·机制3 + LLM交互·机制2: when
# ``post_commit_require_confirmation`` is on, the merged extraction is
# stashed as a proposal; these endpoints are the user's apply/discard.


@router.get("/state-review/{chapter_id}/pending")
def list_pending_extractions(chapter_id: str) -> dict:
    with sqlite3.connect(get_db_path()) as con:
        con.row_factory = sqlite3.Row
        try:
            rows = con.execute(
                "SELECT proposal_id, project_id, chapter_id, chapter_num, "
                "parsed_json, llm_model, status, created_at "
                "FROM pending_state_extractions "
                "WHERE chapter_id = ? AND status = 'pending' "
                "ORDER BY created_at DESC",
                (chapter_id,),
            ).fetchall()
        except sqlite3.OperationalError:
            return {"items": []}
    items = []
    for r in rows:
        d = dict(r)
        try:
            d["parsed"] = json.loads(d.pop("parsed_json") or "{}")
        except Exception:
            d["parsed"] = {}
        items.append(d)
    return {"items": items}


@router.post("/state-review/proposals/{proposal_id}/apply")
async def apply_pending_extraction(proposal_id: str) -> dict:
    """User confirmed — persist every product of the merged extraction
    through the same apply path the direct-write mode uses."""
    db_path = get_db_path()
    with sqlite3.connect(db_path) as con:
        con.row_factory = sqlite3.Row
        row = con.execute(
            "SELECT * FROM pending_state_extractions WHERE proposal_id = ?",
            (proposal_id,),
        ).fetchone()
    if not row:
        raise HTTPException(404, "proposal not found")
    if row["status"] != "pending":
        raise HTTPException(409, f"proposal already {row['status']}")
    try:
        parsed = json.loads(row["parsed_json"] or "{}")
    except Exception:
        raise HTTPException(500, "stored proposal is not valid JSON")

    from ..services.commit_pipeline.sub_tasks import SubTaskContext
    from ..services.commit_pipeline.sub_tasks.state_extractor import (
        _apply_parsed,
    )
    ctx = SubTaskContext(
        project_id=row["project_id"], chapter_id=row["chapter_id"],
        db_path=db_path, chapter_num=row["chapter_num"],
    )
    result = _apply_parsed(
        ctx, row["chapter_num"], parsed, row["llm_model"] or "",
    )
    with sqlite3.connect(db_path) as con:
        con.execute(
            "UPDATE pending_state_extractions SET status='applied', "
            "resolved_at=CURRENT_TIMESTAMP WHERE proposal_id = ?",
            (proposal_id,),
        )
        con.commit()
    return {"ok": True, "proposal_id": proposal_id, **result}


@router.post("/state-review/proposals/{proposal_id}/discard")
def discard_pending_extraction(proposal_id: str) -> dict:
    with sqlite3.connect(get_db_path()) as con:
        cur = con.execute(
            "UPDATE pending_state_extractions SET status='discarded', "
            "resolved_at=CURRENT_TIMESTAMP "
            "WHERE proposal_id = ? AND status = 'pending'",
            (proposal_id,),
        )
        con.commit()
    if cur.rowcount == 0:
        raise HTTPException(404, "no pending proposal with that id")
    return {"ok": True, "proposal_id": proposal_id, "status": "discarded"}


# ─────────── Tab 1: state changes ───────────


@router.get("/state-review/{chapter_id}/state")
def list_state_changes(chapter_id: str) -> dict:
    """Tab 1 — every SPO change recorded for this chapter."""
    db = get_db_path()
    # Resolve chapter_num from chapters table; review queries by chapter_num.
    with sqlite3.connect(db) as con:
        ch_row = con.execute(
            "SELECT chapter_num FROM chapters WHERE chapter_id = ?",
            (chapter_id,),
        ).fetchone()
        if not ch_row:
            return {"chapter_id": chapter_id, "changes": []}
        chapter_num = ch_row[0]
        con.row_factory = sqlite3.Row
        rows = con.execute(
            "SELECT * FROM state_change_log "
            "WHERE change_chapter = ? AND change_type = 'state_change' "
            "ORDER BY recorded_at",
            (chapter_num,),
        ).fetchall()
    return {
        "chapter_id":  chapter_id,
        "chapter_num": chapter_num,
        "changes":     [dict(r) for r in rows],
    }


@router.post("/state-changes")
def create_state_change(body: dict = Body(...)) -> dict:
    """Manual SPO insert. Mirrors into truth_current_state +
    state_change_log."""
    required = ("project_id", "subject", "predicate", "new_value", "change_chapter")
    for k in required:
        if not body.get(k):
            raise HTTPException(400, f"missing required field: {k}")
    project_id = body["project_id"]
    subject = body["subject"]
    subject_type = body.get("subject_type", "character")
    predicate = body["predicate"]
    new_value = body["new_value"]
    chapter_num = int(body["change_chapter"])
    trigger = body.get("trigger", "")
    db = get_db_path()
    with sqlite3.connect(db) as con:
        # Find + supersede prior active row.
        existing = con.execute(
            "SELECT triple_id, object FROM truth_current_state "
            "WHERE project_id = ? AND subject = ? AND predicate = ? "
            "AND valid_to_chapter IS NULL",
            (project_id, subject, predicate),
        ).fetchone()
        old_value = existing[1] if existing else None
        if existing:
            con.execute(
                "UPDATE truth_current_state SET valid_to_chapter = ? "
                "WHERE triple_id = ?",
                (max(1, chapter_num - 1), existing[0]),
            )
        tid = f"tcs_{uuid.uuid4().hex[:12]}"
        con.execute(
            "INSERT INTO truth_current_state "
            "(triple_id, project_id, subject, subject_type, "
            " predicate, object, valid_from_chapter) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (tid, project_id, subject, subject_type,
             predicate, new_value, chapter_num),
        )
        lid = _new_log_id()
        con.execute(
            "INSERT INTO state_change_log "
            "(log_id, project_id, subject, subject_type, predicate, "
            " old_value, new_value, change_chapter, trigger, source) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'user_manual')",
            (lid, project_id, subject, subject_type, predicate,
             old_value, new_value, chapter_num, trigger),
        )
        con.commit()
    return {"ok": True, "log_id": lid, "triple_id": tid}


@router.put("/state-changes/{log_id}")
def correct_state_change(log_id: str, body: dict = Body(...)) -> dict:
    """Edit a previously-recorded change. Appends a 'correction' log
    row and re-supersedes truth_current_state."""
    new_value = body.get("new_value")
    if new_value is None:
        raise HTTPException(400, "missing new_value")
    db = get_db_path()
    with sqlite3.connect(db) as con:
        con.row_factory = sqlite3.Row
        orig = con.execute(
            "SELECT * FROM state_change_log WHERE log_id = ?", (log_id,),
        ).fetchone()
        if not orig:
            raise HTTPException(404, f"log_id {log_id!r} not found")
        project_id = orig["project_id"]
        subject = orig["subject"]
        predicate = orig["predicate"]
        chapter_num = orig["change_chapter"]
        old_value = orig["new_value"]
        # Find current active state row to supersede.
        current = con.execute(
            "SELECT triple_id FROM truth_current_state "
            "WHERE project_id = ? AND subject = ? AND predicate = ? "
            "AND valid_to_chapter IS NULL",
            (project_id, subject, predicate),
        ).fetchone()
        if current:
            con.execute(
                "UPDATE truth_current_state SET valid_to_chapter = ? "
                "WHERE triple_id = ?",
                (max(1, chapter_num - 1), current[0]),
            )
        tid = f"tcs_{uuid.uuid4().hex[:12]}"
        con.execute(
            "INSERT INTO truth_current_state "
            "(triple_id, project_id, subject, subject_type, predicate, "
            " object, valid_from_chapter) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (tid, project_id, subject, orig["subject_type"], predicate,
             new_value, chapter_num),
        )
        new_log_id = _new_log_id()
        con.execute(
            "INSERT INTO state_change_log "
            "(log_id, project_id, subject, subject_type, predicate, "
            " old_value, new_value, change_chapter, trigger, source, "
            " change_type, parent_log_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'user_correction', "
            " 'correction', ?)",
            (new_log_id, project_id, subject, orig["subject_type"],
             predicate, old_value, new_value, chapter_num,
             body.get("trigger", "user correction"), log_id),
        )
        con.commit()
    return {"ok": True, "log_id": new_log_id, "parent_log_id": log_id}


@router.post("/state-changes/{log_id}/cancel")
def cancel_state_change(log_id: str) -> dict:
    """Append a cancellation row (new_value=NULL) + drop the current
    truth_current_state row (set valid_to to chapter_num so the state
    rebuild at K-1 still sees the prior value).
    """
    db = get_db_path()
    with sqlite3.connect(db) as con:
        con.row_factory = sqlite3.Row
        orig = con.execute(
            "SELECT * FROM state_change_log WHERE log_id = ?", (log_id,),
        ).fetchone()
        if not orig:
            raise HTTPException(404, f"log_id {log_id!r} not found")
        project_id = orig["project_id"]
        subject = orig["subject"]
        predicate = orig["predicate"]
        chapter_num = orig["change_chapter"]
        con.execute(
            "UPDATE truth_current_state SET valid_to_chapter = ? "
            "WHERE project_id = ? AND subject = ? AND predicate = ? "
            "AND valid_from_chapter = ? AND valid_to_chapter IS NULL",
            (max(1, chapter_num - 1), project_id, subject, predicate,
             chapter_num),
        )
        new_log_id = _new_log_id()
        con.execute(
            "INSERT INTO state_change_log "
            "(log_id, project_id, subject, subject_type, predicate, "
            " old_value, new_value, change_chapter, trigger, source, "
            " change_type, parent_log_id) "
            "VALUES (?, ?, ?, ?, ?, ?, NULL, ?, 'user cancellation', "
            " 'user_correction', 'cancellation', ?)",
            (new_log_id, project_id, subject, orig["subject_type"],
             predicate, orig["new_value"], chapter_num, log_id),
        )
        con.commit()
    return {"ok": True, "log_id": new_log_id, "cancelled": log_id}


# ─────────── Tab 2: ledger ───────────


@router.get("/state-review/{chapter_id}/ledger")
def list_ledger_changes(chapter_id: str) -> dict:
    db = get_db_path()
    with sqlite3.connect(db) as con:
        ch_row = con.execute(
            "SELECT chapter_num FROM chapters WHERE chapter_id = ?",
            (chapter_id,),
        ).fetchone()
        if not ch_row:
            return {"chapter_id": chapter_id, "changes": []}
        chapter_num = ch_row[0]
        con.row_factory = sqlite3.Row
        rows = con.execute(
            "SELECT * FROM character_ledger WHERE chapter_num = ? "
            "ORDER BY created_at",
            (chapter_num,),
        ).fetchall()
    return {
        "chapter_id":  chapter_id,
        "chapter_num": chapter_num,
        "changes":     [dict(r) for r in rows],
    }


@router.post("/ledger-changes")
def create_ledger_change(body: dict = Body(...)) -> dict:
    for k in ("project_id", "character_name", "chapter_num", "key", "delta"):
        if body.get(k) is None:
            raise HTTPException(400, f"missing required field: {k}")
    db = get_db_path()
    char = body["character_name"]
    key = body["key"]
    delta = int(body["delta"])
    op = "add" if delta > 0 else "subtract" if delta < 0 else "set"
    with sqlite3.connect(db) as con:
        prior = con.execute(
            "SELECT COALESCE(SUM(delta), 0) FROM character_ledger "
            "WHERE project_id = ? AND character_name = ? AND key = ?",
            (body["project_id"], char, key),
        ).fetchone()
        new_value = int(prior[0]) + delta
        lid = f"lg_{uuid.uuid4().hex[:12]}"
        con.execute(
            "INSERT INTO character_ledger "
            "(ledger_id, project_id, character_name, chapter_num, category, "
            " key, operation, delta, new_value, reason) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (lid, body["project_id"], char, int(body["chapter_num"]),
             body.get("category", "resource"), key, op, abs(delta),
             new_value, body.get("trigger", "manual entry")),
        )
        con.commit()
    return {"ok": True, "ledger_id": lid, "new_value": new_value}


@router.delete("/ledger-changes/{ledger_id}")
def delete_ledger_change(ledger_id: str) -> dict:
    """Append a reverse-delta entry instead of physically deleting —
    keeps the history intact."""
    db = get_db_path()
    with sqlite3.connect(db) as con:
        con.row_factory = sqlite3.Row
        orig = con.execute(
            "SELECT * FROM character_ledger WHERE ledger_id = ?",
            (ledger_id,),
        ).fetchone()
        if not orig:
            raise HTTPException(404, f"ledger_id {ledger_id!r} not found")
        # Compute current balance and apply reverse.
        prior = con.execute(
            "SELECT COALESCE(SUM(delta), 0) FROM character_ledger "
            "WHERE project_id = ? AND character_name = ? AND key = ?",
            (orig["project_id"], orig["character_name"], orig["key"]),
        ).fetchone()
        orig_delta = orig["delta"]
        if orig["operation"] == "subtract":
            orig_delta = -orig_delta
        reverse = -orig_delta
        new_value = int(prior[0]) + reverse
        lid = f"lg_{uuid.uuid4().hex[:12]}"
        con.execute(
            "INSERT INTO character_ledger "
            "(ledger_id, project_id, character_name, chapter_num, category, "
            " key, operation, delta, new_value, reason) "
            "VALUES (?, ?, ?, ?, ?, ?, 'add', ?, ?, ?)",
            (lid, orig["project_id"], orig["character_name"],
             orig["chapter_num"], orig["category"], orig["key"],
             abs(reverse), new_value, f"reverse of {ledger_id}"),
        )
        con.commit()
    return {"ok": True, "reversal_id": lid, "reversed": ledger_id,
            "new_value": new_value}


# ─────────── Tab 3: emotion ───────────


@router.get("/state-review/{chapter_id}/emotion")
def list_emotion_changes(chapter_id: str) -> dict:
    db = get_db_path()
    with sqlite3.connect(db) as con:
        ch_row = con.execute(
            "SELECT chapter_num FROM chapters WHERE chapter_id = ?",
            (chapter_id,),
        ).fetchone()
        if not ch_row:
            return {"chapter_id": chapter_id, "changes": []}
        chapter_num = ch_row[0]
        con.row_factory = sqlite3.Row
        rows = con.execute(
            "SELECT * FROM emotion_arcs WHERE chapter_num = ? "
            "ORDER BY created_at",
            (chapter_num,),
        ).fetchall()
    return {
        "chapter_id":  chapter_id,
        "chapter_num": chapter_num,
        "changes":     [dict(r) for r in rows],
    }


@router.post("/emotion-arcs")
def create_emotion_arc(body: dict = Body(...)) -> dict:
    for k in ("project_id", "character_name", "chapter_num", "to_state"):
        if not body.get(k):
            raise HTTPException(400, f"missing required field: {k}")
    db = get_db_path()
    aid = f"em_{uuid.uuid4().hex[:12]}"
    with sqlite3.connect(db) as con:
        con.execute(
            "INSERT INTO emotion_arcs "
            "(arc_id, project_id, character_name, chapter_num, "
            " from_state, to_state, trigger) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (aid, body["project_id"], body["character_name"],
             int(body["chapter_num"]),
             body.get("from_state", ""), body["to_state"],
             body.get("trigger", "")),
        )
        con.commit()
    return {"ok": True, "arc_id": aid}


@router.delete("/emotion-arcs/{arc_id}")
def cancel_emotion_arc(arc_id: str) -> dict:
    """Append a cancellation arc (from_state = original to_state,
    to_state = '取消') rather than physical delete."""
    db = get_db_path()
    with sqlite3.connect(db) as con:
        con.row_factory = sqlite3.Row
        orig = con.execute(
            "SELECT * FROM emotion_arcs WHERE arc_id = ?", (arc_id,),
        ).fetchone()
        if not orig:
            raise HTTPException(404, f"arc_id {arc_id!r} not found")
        new_id = f"em_{uuid.uuid4().hex[:12]}"
        con.execute(
            "INSERT INTO emotion_arcs "
            "(arc_id, project_id, character_name, chapter_num, "
            " from_state, to_state, trigger) "
            "VALUES (?, ?, ?, ?, ?, '__cancelled__', ?)",
            (new_id, orig["project_id"], orig["character_name"],
             orig["chapter_num"], orig["to_state"],
             f"cancellation of {arc_id}"),
        )
        con.commit()
    return {"ok": True, "cancellation_id": new_id, "cancelled": arc_id}


# ─────────── Tab 4: entity review queue ───────────


@router.get("/state-review/{chapter_id}/entities")
def list_entity_review(chapter_id: str) -> dict:
    """Tab 4 — both the pending review queue AND the auto-created
    entities the user might want to second-guess."""
    db = get_db_path()
    with sqlite3.connect(db) as con:
        ch_row = con.execute(
            "SELECT chapter_num FROM chapters WHERE chapter_id = ?",
            (chapter_id,),
        ).fetchone()
        chapter_num = ch_row[0] if ch_row else None
        con.row_factory = sqlite3.Row
        pending = con.execute(
            "SELECT * FROM entity_review_queue "
            "WHERE chapter_id = ? AND status = 'pending'",
            (chapter_id,),
        ).fetchall()
        auto = []
        if chapter_num is not None:
            auto = con.execute(
                "SELECT entity_id, name, entity_type, aliases_json, "
                "       introduced_chapter, mention_count, auto_created "
                "FROM storyland_entities "
                "WHERE auto_created = 1 AND introduced_chapter = ?",
                (chapter_num,),
            ).fetchall()
    return {
        "chapter_id":         chapter_id,
        "pending_review":     [dict(r) for r in pending],
        "auto_created":       [dict(r) for r in auto],
    }


@router.post("/entity-review-queue/{queue_id}/decision")
def decide_entity(queue_id: str, body: dict = Body(...)) -> dict:
    """Three decisions: merge / create / discard."""
    action = (body.get("action") or "").strip()
    if action not in ("merge", "create", "discard"):
        raise HTTPException(400, "action must be merge/create/discard")
    db = get_db_path()
    with sqlite3.connect(db) as con:
        con.row_factory = sqlite3.Row
        row = con.execute(
            "SELECT * FROM entity_review_queue WHERE queue_id = ?",
            (queue_id,),
        ).fetchone()
        if not row:
            raise HTTPException(404, f"queue_id {queue_id!r} not found")
        if row["status"] != "pending":
            raise HTTPException(400, f"queue item already {row['status']}")

        new_status = {"merge": "merged", "create": "created_separately",
                       "discard": "discarded"}[action]
        # Side effects:
        if action == "merge":
            target_id = body.get("target_entity_id") or row["similar_existing_entity_id"]
            if not target_id:
                raise HTTPException(400, "merge requires target_entity_id")
            # Add candidate name + its aliases as new aliases on target.
            try:
                aliases = json.loads(row["candidate_aliases_json"] or "[]")
            except json.JSONDecodeError:
                aliases = []
            cur = con.execute(
                "SELECT aliases_json FROM storyland_entities WHERE entity_id = ?",
                (target_id,),
            ).fetchone()
            if not cur:
                raise HTTPException(404, f"target_entity_id {target_id!r} not found")
            try:
                current_aliases = json.loads(cur[0] or "[]")
            except json.JSONDecodeError:
                current_aliases = []
            for a in [row["candidate_name"]] + aliases:
                if a and a not in current_aliases:
                    current_aliases.append(a)
            con.execute(
                "UPDATE storyland_entities SET aliases_json = ? "
                "WHERE entity_id = ?",
                (json.dumps(current_aliases, ensure_ascii=False), target_id),
            )
        elif action == "create":
            # Promote to a fresh storyland_entities row.
            try:
                aliases = json.loads(row["candidate_aliases_json"] or "[]")
            except json.JSONDecodeError:
                aliases = []
            eid = f"ent_{uuid.uuid4().hex[:12]}"
            ch_row = con.execute(
                "SELECT chapter_num FROM chapters WHERE chapter_id = ?",
                (row["chapter_id"],),
            ).fetchone()
            con.execute(
                "INSERT INTO storyland_entities "
                "(entity_id, project_id, name, entity_type, description, "
                " introduced_chapter, aliases_json, source, auto_created) "
                "VALUES (?, ?, ?, ?, '', ?, ?, 'manual', 0)",
                (eid, row["project_id"], row["candidate_name"],
                 row["candidate_type"], ch_row[0] if ch_row else None,
                 json.dumps(aliases, ensure_ascii=False)),
            )
        # discard → no side effect; just mark.

        con.execute(
            "UPDATE entity_review_queue SET status = ?, "
            "user_decision_at = CURRENT_TIMESTAMP WHERE queue_id = ?",
            (new_status, queue_id),
        )
        con.commit()
    return {"ok": True, "queue_id": queue_id, "new_status": new_status}


# ─────────── Entities — manual create / merge ───────────


@router.post("/entities")
def create_entity(body: dict = Body(...)) -> dict:
    for k in ("project_id", "name", "entity_type"):
        if not body.get(k):
            raise HTTPException(400, f"missing required field: {k}")
    if body["entity_type"] not in {"character", "location", "item",
                                     "organization", "concept"}:
        raise HTTPException(400, f"invalid entity_type {body['entity_type']!r}")
    db = get_db_path()
    eid = f"ent_{uuid.uuid4().hex[:12]}"
    with sqlite3.connect(db) as con:
        con.execute(
            "INSERT INTO storyland_entities "
            "(entity_id, project_id, name, entity_type, description, "
            " introduced_chapter, aliases_json, source, auto_created) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 'manual', 0)",
            (eid, body["project_id"], body["name"], body["entity_type"],
             body.get("description", ""), body.get("introduced_chapter"),
             json.dumps(body.get("aliases", []), ensure_ascii=False)),
        )
        con.commit()
    return {"ok": True, "entity_id": eid}


@router.post("/entities/merge")
def merge_entities(body: dict = Body(...)) -> dict:
    """Collapse ``source_id`` into ``target_id``. ``source_id``'s name +
    aliases are appended to ``target_id.aliases_json``; the source row
    is deleted."""
    sid = body.get("source_id")
    tid = body.get("target_id")
    if not sid or not tid:
        raise HTTPException(400, "source_id + target_id required")
    if sid == tid:
        raise HTTPException(400, "cannot merge entity with itself")
    db = get_db_path()
    with sqlite3.connect(db) as con:
        con.row_factory = sqlite3.Row
        src = con.execute(
            "SELECT * FROM storyland_entities WHERE entity_id = ?", (sid,),
        ).fetchone()
        tgt = con.execute(
            "SELECT * FROM storyland_entities WHERE entity_id = ?", (tid,),
        ).fetchone()
        if not src or not tgt:
            raise HTTPException(404, "one or both entities not found")
        try:
            src_aliases = json.loads(src["aliases_json"] or "[]")
            tgt_aliases = json.loads(tgt["aliases_json"] or "[]")
        except json.JSONDecodeError:
            src_aliases, tgt_aliases = [], []
        for a in [src["name"]] + src_aliases:
            if a and a not in tgt_aliases:
                tgt_aliases.append(a)
        con.execute(
            "UPDATE storyland_entities SET aliases_json = ? WHERE entity_id = ?",
            (json.dumps(tgt_aliases, ensure_ascii=False), tid),
        )
        con.execute(
            "DELETE FROM storyland_entities WHERE entity_id = ?", (sid,),
        )
        con.commit()
    return {"ok": True, "merged_into": tid, "removed": sid}
