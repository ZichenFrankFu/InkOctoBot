"""Cross-file validators for the Truth File system (C3).

12 rules covering data integrity across the 7 truth files. Designed to be
callable from TruthFileStore.apply_deltas (via validate=True) or
standalone for batch state audits.

Rules are split into three layers:
  1. Within-delta (pure-Python; no DB access)
  2. Delta vs caller-supplied known_characters
  3. Delta vs DB state (queries through the store's connection)
"""
from __future__ import annotations

import logging
import sqlite3
from typing import TYPE_CHECKING, Iterable

from knowledge.truth.schemas import (
    HookDelta, HookStatus, NumericalReconciliation, TruthDeltas,
    ValidationIssue, ValidationReport,
)

if TYPE_CHECKING:
    from knowledge.truth.store import TruthFileStore

logger = logging.getLogger("inkoctobot.knowledge.truth.validators")


# ──────────────────────────────────────────────────────────────
# Top-level entry
# ──────────────────────────────────────────────────────────────

def validate_deltas(
    store: "TruthFileStore",
    deltas: TruthDeltas,
    *,
    known_characters: set[str] | None = None,
    allow_backfill: bool = False,
) -> list[ValidationIssue]:
    """Full pre-apply validation. Issues are collected, never raised."""
    issues: list[ValidationIssue] = []

    if known_characters is None:
        known_characters = _resolve_known_characters(store)

    # ── Layer 1: within-delta ────────────────────
    issues.extend(_rule_hook_no_duplicate_id(deltas))
    issues.extend(_rule_ledger_closed_equation(deltas))

    # ── Layer 2: known_characters cross-ref ──────
    if known_characters is not None:
        issues.extend(_rule_xref_character_in_emotion(deltas, known_characters))
        issues.extend(_rule_xref_character_in_relation(deltas, known_characters))

    # ── Layer 3: DB queries ──────────────────────
    with sqlite3.connect(store.db_path) as conn:
        conn.execute("PRAGMA foreign_keys = ON;")
        if not allow_backfill:
            issues.extend(_rule_chapter_monotonic(conn, store.project_id, deltas))
        issues.extend(_rule_hook_orphan_progress(conn, store.project_id, deltas))
        issues.extend(_rule_hook_transition_valid(conn, store.project_id, deltas))
        issues.extend(_rule_ledger_matches_current(conn, store.project_id, deltas))
        issues.extend(_rule_xref_hook_in_subplot(conn, store.project_id, deltas))
        issues.extend(_rule_state_no_conflicting_triple(deltas))
        issues.extend(_rule_relation_symmetric_drift(conn, store.project_id, deltas))
        issues.extend(_rule_subplot_hook_resolved_should_advance(
            conn, store.project_id, deltas,
        ))

    return issues


def validate_state(
    store: "TruthFileStore",
    *,
    known_characters: set[str] | None = None,
) -> ValidationReport:
    """Audit current persisted state (no pending deltas)."""
    if known_characters is None:
        known_characters = _resolve_known_characters(store)

    issues: list[ValidationIssue] = []
    stats: dict[str, int] = {}
    with sqlite3.connect(store.db_path) as conn:
        conn.execute("PRAGMA foreign_keys = ON;")
        for table in (
            "truth_current_state", "character_ledger", "pending_hooks",
            "hook_events", "subplot_threads", "emotion_arcs",
            "character_relations", "truth_apply_log",
        ):
            row = conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE project_id = ?",
                (store.project_id,),
            ).fetchone()
            stats[table] = row[0]

        # Static audit: orphaned subplot hook references, asymmetric relations
        issues.extend(_audit_orphaned_subplot_hooks(conn, store.project_id))
        if known_characters is not None:
            issues.extend(_audit_emotion_arcs_known_characters(
                conn, store.project_id, known_characters,
            ))

    passed = not any(i.severity == "error" for i in issues)
    return ValidationReport(passed=passed, issues=issues, stats=stats)


# ──────────────────────────────────────────────────────────────
# Known-character resolver
# ──────────────────────────────────────────────────────────────

def _resolve_known_characters(store: "TruthFileStore") -> set[str] | None:
    """Best-effort lookup of character names for this project.

    Tries the live data_api router; falls back to None which makes
    character-existence rules a no-op.
    """
    try:
        from ui.backend.app.routers.json_storage_api import _list  # type: ignore
        rows = _list("characters", filter_key="project_id",
                     filter_value=store.project_id)
        names: set[str] = set()
        for r in rows:
            name = r.get("name", "")
            if name:
                names.add(name)
            for a in (r.get("aliases") or []):
                if a:
                    names.add(a)
        return names if names else None
    except Exception as exc:
        logger.debug("known_characters resolver fallback: %s", exc)
        return None


# ──────────────────────────────────────────────────────────────
# Layer 1 — within-delta rules
# ──────────────────────────────────────────────────────────────

def _rule_hook_no_duplicate_id(deltas: TruthDeltas) -> list[ValidationIssue]:
    seen: set[str] = set()
    dups: list[ValidationIssue] = []
    for idx, h in enumerate(deltas.hook_deltas):
        if h.hook_id and h.hook_id in seen:
            dups.append(ValidationIssue(
                rule_id="hook.no_duplicate_id", severity="error",
                message=f"hook_id={h.hook_id!r} repeats within deltas",
                location=f"hook_deltas[{idx}]",
            ))
        if h.hook_id:
            seen.add(h.hook_id)
    return dups


def _rule_ledger_closed_equation(deltas: TruthDeltas) -> list[ValidationIssue]:
    out: list[ValidationIssue] = []
    for idx, r in enumerate(deltas.particle_reconciliations):
        if r.operation == "set":
            # set operation: new_value is the target; equation is trivially closed
            continue
        if r.old_value + r.delta != r.new_value:
            out.append(ValidationIssue(
                rule_id="ledger.closed_equation", severity="error",
                message=(
                    f"{r.character}/{r.resource}: "
                    f"{r.old_value}{r.operation}{r.delta} != {r.new_value}"
                ),
                location=f"particle_reconciliations[{idx}]",
            ))
    return out


def _rule_state_no_conflicting_triple(deltas: TruthDeltas) -> list[ValidationIssue]:
    """Within deltas, same (subject, predicate) cannot have both upsert and
    invalidate at the same chapter — they cancel each other and signal
    a likely modeling bug."""
    out: list[ValidationIssue] = []
    upsert_keys: set[tuple[str, str, int]] = set()
    invalidate_keys: set[tuple[str, str, int]] = set()
    for p in deltas.current_state_patches:
        key = (p.subject, p.predicate, p.valid_from_chapter)
        if p.action == "upsert":
            upsert_keys.add(key)
        else:
            invalidate_keys.add(key)
    for key in upsert_keys & invalidate_keys:
        out.append(ValidationIssue(
            rule_id="state.no_conflicting_triple", severity="warning",
            message=f"({key[0]}, {key[1]}) has both upsert and invalidate "
                    f"at chapter {key[2]}",
            location="current_state_patches",
        ))
    return out


# ──────────────────────────────────────────────────────────────
# Layer 2 — known_characters cross-ref
# ──────────────────────────────────────────────────────────────

def _rule_xref_character_in_emotion(
    deltas: TruthDeltas, known: set[str],
) -> list[ValidationIssue]:
    out: list[ValidationIssue] = []
    for idx, e in enumerate(deltas.emotion_arc_entries):
        if e.character not in known:
            out.append(ValidationIssue(
                rule_id="xref.character_in_emotion", severity="error",
                message=f"emotion arc references unknown character "
                        f"{e.character!r}",
                location=f"emotion_arc_entries[{idx}]",
            ))
    return out


def _rule_xref_character_in_relation(
    deltas: TruthDeltas, known: set[str],
) -> list[ValidationIssue]:
    out: list[ValidationIssue] = []
    for idx, r in enumerate(deltas.character_relation_updates):
        for side, name in (("a", r.character_a), ("b", r.character_b)):
            if name not in known:
                out.append(ValidationIssue(
                    rule_id="xref.character_in_relation", severity="error",
                    message=f"relation character_{side}={name!r} unknown",
                    location=f"character_relation_updates[{idx}]",
                ))
    return out


# ──────────────────────────────────────────────────────────────
# Layer 3 — DB-aware rules
# ──────────────────────────────────────────────────────────────

def _rule_chapter_monotonic(
    conn: sqlite3.Connection, project_id: str, deltas: TruthDeltas,
) -> list[ValidationIssue]:
    row = conn.execute(
        "SELECT MAX(chapter_num) FROM truth_apply_log WHERE project_id=?",
        (project_id,),
    ).fetchone()
    last_applied = row[0] if row and row[0] is not None else 0
    if deltas.chapter_num < last_applied:
        return [ValidationIssue(
            rule_id="chapter.monotonic", severity="error",
            message=(
                f"chapter {deltas.chapter_num} < last applied "
                f"{last_applied}; pass allow_backfill=True for backfill"
            ),
            location="chapter_num",
        )]
    return []


def _rule_hook_orphan_progress(
    conn: sqlite3.Connection, project_id: str, deltas: TruthDeltas,
) -> list[ValidationIssue]:
    out: list[ValidationIssue] = []
    new_ids = {h.hook_id for h in deltas.hook_deltas
               if h.action == "new" and h.hook_id}
    for idx, h in enumerate(deltas.hook_deltas):
        if h.action == "new":
            continue
        if not h.hook_id:
            out.append(ValidationIssue(
                rule_id="hook.no_orphan_progress", severity="error",
                message=f"action={h.action} requires hook_id",
                location=f"hook_deltas[{idx}]",
            ))
            continue
        if h.hook_id in new_ids:
            continue
        row = conn.execute(
            "SELECT 1 FROM pending_hooks WHERE hook_id=? AND project_id=?",
            (h.hook_id, project_id),
        ).fetchone()
        if not row:
            out.append(ValidationIssue(
                rule_id="hook.no_orphan_progress", severity="error",
                message=f"action={h.action} references unknown hook_id "
                        f"{h.hook_id!r}",
                location=f"hook_deltas[{idx}]",
            ))
    return out


def _rule_hook_transition_valid(
    conn: sqlite3.Connection, project_id: str, deltas: TruthDeltas,
) -> list[ValidationIssue]:
    """Reject mention/progress/resolve/abandon against terminal hooks
    (resolved | abandoned)."""
    out: list[ValidationIssue] = []
    terminal = {HookStatus.resolved.value, HookStatus.abandoned.value}
    for idx, h in enumerate(deltas.hook_deltas):
        if h.action == "new" or not h.hook_id:
            continue
        row = conn.execute(
            "SELECT status FROM pending_hooks WHERE hook_id=? AND project_id=?",
            (h.hook_id, project_id),
        ).fetchone()
        if not row:
            continue  # orphan rule handles missing
        if row[0] in terminal:
            out.append(ValidationIssue(
                rule_id="hook.transition_valid", severity="error",
                message=f"cannot apply action={h.action} to hook "
                        f"{h.hook_id} in terminal status {row[0]}",
                location=f"hook_deltas[{idx}]",
            ))
    return out


def _rule_ledger_matches_current(
    conn: sqlite3.Connection, project_id: str, deltas: TruthDeltas,
) -> list[ValidationIssue]:
    out: list[ValidationIssue] = []
    for idx, r in enumerate(deltas.particle_reconciliations):
        row = conn.execute(
            """SELECT new_value FROM character_ledger
               WHERE project_id=? AND character_name=? AND key=?
                 AND chapter_num < ?
               ORDER BY chapter_num DESC, created_at DESC LIMIT 1""",
            (project_id, r.character, r.resource, deltas.chapter_num),
        ).fetchone()
        current = row[0] if row else None
        # No prior record: the LLM is initializing — accept regardless.
        if current is None:
            continue
        if current != r.old_value:
            out.append(ValidationIssue(
                rule_id="ledger.matches_current", severity="error",
                message=(
                    f"{r.character}/{r.resource}: old_value={r.old_value} "
                    f"but ledger current={current}"
                ),
                location=f"particle_reconciliations[{idx}]",
            ))
    return out


def _rule_xref_hook_in_subplot(
    conn: sqlite3.Connection, project_id: str, deltas: TruthDeltas,
) -> list[ValidationIssue]:
    """Subplot's related_hook_ids must be (a) created in this batch or
    (b) already in pending_hooks."""
    new_ids = {h.hook_id for h in deltas.hook_deltas
               if h.action == "new" and h.hook_id}
    out: list[ValidationIssue] = []
    for idx, s in enumerate(deltas.subplot_updates):
        for hid in s.related_hook_ids:
            if hid in new_ids:
                continue
            row = conn.execute(
                "SELECT 1 FROM pending_hooks WHERE hook_id=? AND project_id=?",
                (hid, project_id),
            ).fetchone()
            if not row:
                out.append(ValidationIssue(
                    rule_id="xref.hook_in_subplot", severity="error",
                    message=f"subplot {s.name!r} references unknown hook "
                            f"{hid!r}",
                    location=f"subplot_updates[{idx}]",
                ))
    return out


def _rule_relation_symmetric_drift(
    conn: sqlite3.Connection, project_id: str, deltas: TruthDeltas,
) -> list[ValidationIssue]:
    """After applying this batch, check (A,B)/(B,A) sentiment divergence.
    Pre-apply heuristic: compare proposed sentiment against existing
    reverse-direction entry in DB."""
    try:
        import config as _cfg
        threshold = int(
            (_cfg._load_yaml("truth_files.yaml") or {}).get(
                "relation_symmetric_drift_warn", 40,
            )
        )
    except Exception:
        threshold = 40

    out: list[ValidationIssue] = []
    for idx, r in enumerate(deltas.character_relation_updates):
        row = conn.execute(
            """SELECT sentiment_score FROM character_relations
               WHERE project_id=? AND character_a=? AND character_b=?""",
            (project_id, r.character_b, r.character_a),
        ).fetchone()
        if row is not None and abs(row[0] - r.sentiment_score) > threshold:
            out.append(ValidationIssue(
                rule_id="relation.symmetric_sentiment_drift",
                severity="warning",
                message=(
                    f"({r.character_a},{r.character_b}) sentiment "
                    f"{r.sentiment_score} differs from "
                    f"({r.character_b},{r.character_a})={row[0]} by "
                    f">{threshold}"
                ),
                location=f"character_relation_updates[{idx}]",
            ))
    return out


def _rule_subplot_hook_resolved_should_advance(
    conn: sqlite3.Connection, project_id: str, deltas: TruthDeltas,
) -> list[ValidationIssue]:
    """When a hook resolves but its referencing subplots still sit in 'setup',
    surface as info to nudge author/LLM to advance the subplot too."""
    out: list[ValidationIssue] = []
    resolved_ids = {h.hook_id for h in deltas.hook_deltas
                    if h.action == "resolve" and h.hook_id}
    if not resolved_ids:
        return out
    rows = conn.execute(
        """SELECT thread_id, name, status, related_hook_ids_json
           FROM subplot_threads
           WHERE project_id=? AND status='setup'""",
        (project_id,),
    ).fetchall()
    import json
    for thread_id, name, status, hooks_json in rows:
        try:
            related = set(json.loads(hooks_json or "[]"))
        except Exception:
            related = set()
        overlap = related & resolved_ids
        if overlap:
            out.append(ValidationIssue(
                rule_id="subplot.hook_resolved_subplot_should_advance",
                severity="info",
                message=(
                    f"subplot {name!r} still in setup but hooks "
                    f"{sorted(overlap)} were resolved"
                ),
                location=f"subplot_threads[{thread_id}]",
            ))
    return out


# ──────────────────────────────────────────────────────────────
# Helpers for validate_state (no deltas)
# ──────────────────────────────────────────────────────────────

def _audit_orphaned_subplot_hooks(
    conn: sqlite3.Connection, project_id: str,
) -> list[ValidationIssue]:
    """Find subplot rows referencing hook_ids that no longer exist."""
    import json
    out: list[ValidationIssue] = []
    rows = conn.execute(
        """SELECT thread_id, name, related_hook_ids_json
           FROM subplot_threads WHERE project_id=?""",
        (project_id,),
    ).fetchall()
    if not rows:
        return out
    existing = {
        r[0] for r in conn.execute(
            "SELECT hook_id FROM pending_hooks WHERE project_id=?",
            (project_id,),
        )
    }
    for thread_id, name, hooks_json in rows:
        try:
            ids = json.loads(hooks_json or "[]")
        except Exception:
            ids = []
        missing = [h for h in ids if h not in existing]
        if missing:
            out.append(ValidationIssue(
                rule_id="audit.orphaned_subplot_hooks", severity="warning",
                message=f"subplot {name!r} has orphan hooks {missing}",
                location=f"subplot_threads[{thread_id}]",
            ))
    return out


def _audit_emotion_arcs_known_characters(
    conn: sqlite3.Connection, project_id: str, known: set[str],
) -> list[ValidationIssue]:
    out: list[ValidationIssue] = []
    rows = conn.execute(
        """SELECT arc_id, character_name FROM emotion_arcs
           WHERE project_id=?""",
        (project_id,),
    ).fetchall()
    for arc_id, name in rows:
        if name not in known:
            out.append(ValidationIssue(
                rule_id="audit.emotion_arc_unknown_character",
                severity="warning",
                message=f"emotion arc {arc_id} references unknown "
                        f"character {name!r}",
                location=f"emotion_arcs[{arc_id}]",
            ))
    return out


__all__ = [
    "validate_deltas",
    "validate_state",
]
