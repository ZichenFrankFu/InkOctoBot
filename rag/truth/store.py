"""TruthFileStore — the state authority for the Truth File system.

C2 scope: writes only.
  - apply_deltas() with atomic SQLite transaction
  - idempotency via SHA-256 deltas_hash + truth_apply_log unique index
  - _recompute_pressure() scans non-terminal hooks after each apply

Cross-file validation (C3), query API (C4), Markdown export (C4) and
migration (C5) come in later commits. validate=True is accepted by
apply_deltas but silently no-ops until C3.
"""
from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import uuid
from pathlib import Path
from typing import Any

import yaml

from rag.truth import sql as Q
from rag.truth.schemas import (
    ApplyResult, HookDelta, HookStatus, TruthDeltas, ValidationIssue,
)

logger = logging.getLogger("inkoctobot.rag.truth.store")


def _new_id() -> str:
    """12-char uuid hex matching the style used elsewhere in the codebase."""
    return uuid.uuid4().hex[:12]


def _hash_deltas(deltas: TruthDeltas) -> str:
    """Deterministic SHA-256 over TruthDeltas (sorted JSON)."""
    payload = json.dumps(deltas.model_dump(mode="json"), sort_keys=True,
                         ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _load_truth_config() -> dict[str, Any]:
    """Load config/truth_files.yaml — safe fallback if missing."""
    try:
        import config as _cfg
        cfg_dir = Path(_cfg._CONFIG_DIR)
    except Exception:
        cfg_dir = Path(__file__).resolve().parents[2] / "config"
    path = cfg_dir / "truth_files.yaml"
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


class TruthFileStore:
    """7 truth files unified storage + atomic delta application.

    SQLite is canonical; one connection per apply (no shared cursor).
    Markdown rendering and queries are added in C4.
    """

    def __init__(self, project_id: str, db_path: str | None = None) -> None:
        self.project_id = project_id
        self.db_path = db_path or self._default_db_path()
        self._config = _load_truth_config()

    # ─── helpers ──────────────────────────────────────────────

    @staticmethod
    def _default_db_path() -> str:
        try:
            import config as _cfg
            db_rel = (_cfg._paths or {}).get("database", {}).get(
                "path", "data/novels.db"
            )
            return str(Path(_cfg.BASE_DIR) / db_rel)
        except Exception:
            return "data/novels.db"

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA foreign_keys = ON;")
        return conn

    def _pressure_threshold(self, importance: str) -> int:
        h = (self._config.get("hooks") or {})
        if importance == "A":
            return int(h.get("pressure_threshold_importance_a", 3))
        return int(h.get("pressure_threshold_default", 5))

    # ═══════════ public write API ═══════════

    def apply_deltas(
        self, deltas: TruthDeltas, *, validate: bool = False,
        allow_backfill: bool = False,
    ) -> ApplyResult:
        """Atomically apply a TruthDeltas bundle.

        Idempotent: a (project_id, deltas_hash) collision returns the
        cached ApplyResult without re-writing.

        C2 ignores ``validate`` (no validator wired yet). C3 will run
        ``rag.truth.validators.validate_deltas()`` when true.
        """
        deltas_hash = _hash_deltas(deltas)

        # ── Step 1: idempotency check ───────────────────
        with self._connect() as conn:
            row = conn.execute(
                Q.FIND_APPLY_LOG, (self.project_id, deltas_hash),
            ).fetchone()
            if row is not None:
                counts = json.loads(row[2] or "{}")
                issues = [
                    ValidationIssue(**i) for i in json.loads(row[3] or "[]")
                ]
                return ApplyResult(
                    success=True, chapter_num=row[1], applied_counts=counts,
                    cross_ref_issues=issues, deltas_hash=deltas_hash,
                    idempotent_hit=True,
                )

        # ── Step 2: optional pre-validation (C3 will fill) ──
        cross_ref_issues: list[ValidationIssue] = []
        if validate:
            try:
                from rag.truth.validators import validate_deltas
                cross_ref_issues = validate_deltas(self, deltas)
            except ImportError:
                pass  # C3 not yet landed
            # If any error-severity issue, abort before touching DB.
            if any(i.severity == "error" for i in cross_ref_issues):
                return ApplyResult(
                    success=False, chapter_num=deltas.chapter_num,
                    cross_ref_issues=cross_ref_issues,
                    deltas_hash=deltas_hash,
                )

        # ── Step 3: atomic apply in one transaction ─────
        counts: dict[str, int] = {}
        errors: list[str] = []
        conn = self._connect()
        try:
            conn.execute("BEGIN")
            counts["current_state"] = self._apply_state_patches(conn, deltas)
            counts["character_ledger"] = self._apply_reconciliations(conn, deltas)
            hook_id_map = self._apply_hook_deltas(conn, deltas)
            counts["pending_hooks"] = len(deltas.hook_deltas)
            counts["chapter_summaries"] = self._apply_chapter_summary(conn, deltas)
            counts["subplot_threads"] = self._apply_subplots(
                conn, deltas, hook_id_map,
            )
            counts["emotion_arcs"] = self._apply_emotion_arcs(conn, deltas)
            counts["character_relations"] = self._apply_relations(conn, deltas)

            # post-apply: recompute which hooks are now pressured
            pressured = self._recompute_pressure(conn, deltas.chapter_num)
            counts["pressure_transitions"] = pressured

            # idempotency log
            conn.execute(
                Q.INSERT_APPLY_LOG,
                (
                    _new_id(), self.project_id, deltas.chapter_num,
                    deltas_hash, json.dumps(counts),
                    json.dumps([i.model_dump() for i in cross_ref_issues]),
                ),
            )
            conn.commit()
        except sqlite3.Error as exc:
            conn.rollback()
            errors.append(f"{type(exc).__name__}: {exc}")
            logger.exception("apply_deltas rolled back")
            return ApplyResult(
                success=False, chapter_num=deltas.chapter_num,
                applied_counts={}, cross_ref_issues=cross_ref_issues,
                sqlite_errors=errors, deltas_hash=deltas_hash,
            )
        finally:
            conn.close()

        return ApplyResult(
            success=True, chapter_num=deltas.chapter_num,
            applied_counts=counts, cross_ref_issues=cross_ref_issues,
            deltas_hash=deltas_hash, idempotent_hit=False,
        )

    # ═══════════ per-table write helpers ═══════════

    def _apply_state_patches(
        self, conn: sqlite3.Connection, deltas: TruthDeltas,
    ) -> int:
        n = 0
        for p in deltas.current_state_patches:
            if p.action == "upsert":
                new_id = _new_id()
                # Close any existing valid triples for the same (subject, predicate)
                conn.execute(
                    Q.SUPERSEDE_SPO,
                    (
                        max(p.valid_from_chapter - 1, 0), new_id,
                        self.project_id, p.subject, p.predicate, new_id,
                    ),
                )
                conn.execute(
                    Q.INSERT_SPO,
                    (
                        new_id, self.project_id, p.subject, p.predicate,
                        p.object, p.valid_from_chapter, 1.0,
                    ),
                )
                n += 1
            elif p.action == "invalidate":
                conn.execute(
                    Q.INVALIDATE_SPO,
                    (
                        max(p.valid_from_chapter - 1, 0),
                        self.project_id, p.subject, p.predicate, p.object,
                    ),
                )
                n += 1
        return n

    def _apply_reconciliations(
        self, conn: sqlite3.Connection, deltas: TruthDeltas,
    ) -> int:
        n = 0
        for r in deltas.particle_reconciliations:
            conn.execute(
                Q.INSERT_LEDGER,
                (
                    _new_id(), self.project_id, r.character,
                    deltas.chapter_num, "resource", r.resource,
                    r.operation, r.delta, r.new_value,
                    r.reason, r.in_text_evidence,
                ),
            )
            n += 1
        return n

    def _apply_hook_deltas(
        self, conn: sqlite3.Connection, deltas: TruthDeltas,
    ) -> dict[int, str]:
        """Apply hook deltas. Returns map {delta_index → resolved hook_id}
        so subsequent subplots can reference newly-created hooks."""
        resolved: dict[int, str] = {}
        for idx, h in enumerate(deltas.hook_deltas):
            hook_id = h.hook_id
            if h.action == "new":
                hook_id = hook_id or _new_id()
                threshold = self._pressure_threshold(h.importance.value)
                conn.execute(
                    Q.INSERT_HOOK,
                    (
                        hook_id, self.project_id, h.description, "open",
                        h.importance.value, deltas.chapter_num,
                        h.expected_payoff_chapter, deltas.chapter_num,
                        deltas.chapter_num, threshold,
                    ),
                )
                self._write_hook_event(
                    conn, hook_id, deltas.chapter_num, "new",
                    before=None, after="open", evidence=h.evidence,
                )
            else:
                if not hook_id:
                    # Validator (C3) will catch; here we just skip safely.
                    logger.warning(
                        "hook delta action=%s missing hook_id; skipping",
                        h.action,
                    )
                    continue
                row = conn.execute(Q.GET_HOOK, (hook_id,)).fetchone()
                if not row:
                    logger.warning(
                        "hook delta references unknown hook_id=%s; skipping",
                        hook_id,
                    )
                    continue
                before_status = row[3]

                if h.action == "mention":
                    conn.execute(
                        Q.UPDATE_HOOK_MENTION,
                        (deltas.chapter_num, hook_id),
                    )
                    self._write_hook_event(
                        conn, hook_id, deltas.chapter_num, "mention",
                        before=before_status, after=before_status,
                        evidence=h.evidence,
                    )
                elif h.action == "progress":
                    new_status = self._progress_transition(before_status)
                    conn.execute(
                        Q.UPDATE_HOOK_PROGRESS,
                        (
                            new_status, deltas.chapter_num,
                            deltas.chapter_num, hook_id,
                        ),
                    )
                    self._write_hook_event(
                        conn, hook_id, deltas.chapter_num, "progress",
                        before=before_status, after=new_status,
                        evidence=h.evidence,
                    )
                elif h.action == "resolve":
                    conn.execute(
                        Q.UPDATE_HOOK_STATUS, ("resolved", hook_id),
                    )
                    self._write_hook_event(
                        conn, hook_id, deltas.chapter_num, "resolve",
                        before=before_status, after="resolved",
                        evidence=h.evidence,
                    )
                elif h.action == "abandon":
                    conn.execute(
                        Q.UPDATE_HOOK_STATUS, ("abandoned", hook_id),
                    )
                    self._write_hook_event(
                        conn, hook_id, deltas.chapter_num, "abandon",
                        before=before_status, after="abandoned",
                        evidence=h.evidence,
                    )

            if hook_id:
                resolved[idx] = hook_id
        return resolved

    @staticmethod
    def _progress_transition(before: str) -> str:
        """open → progressing; progressing/pressured → progressing.
        near_payoff stays. resolved/abandoned should be caught by validator."""
        if before in (HookStatus.open.value, HookStatus.pressured.value):
            return HookStatus.progressing.value
        if before == HookStatus.progressing.value:
            return HookStatus.progressing.value
        return before  # near_payoff stays; terminal states untouched

    def _write_hook_event(
        self, conn: sqlite3.Connection, hook_id: str, chapter_num: int,
        action: str, *, before: str | None, after: str | None, evidence: str,
    ) -> None:
        conn.execute(
            Q.INSERT_HOOK_EVENT,
            (
                _new_id(), hook_id, self.project_id, chapter_num,
                action, before, after, evidence,
            ),
        )

    def _apply_chapter_summary(
        self, conn: sqlite3.Connection, deltas: TruthDeltas,
    ) -> int:
        if deltas.chapter_summary is None:
            return 0
        cs = deltas.chapter_summary
        character_states = {}
        if cs.pov_character:
            character_states["pov"] = cs.pov_character
        if cs.mood:
            character_states["mood"] = cs.mood
        conn.execute(
            Q.UPSERT_CHAPTER_SUMMARY,
            (
                _new_id(), self.project_id, deltas.chapter_num,
                cs.summary, json.dumps(cs.key_events, ensure_ascii=False),
                json.dumps(character_states, ensure_ascii=False),
            ),
        )
        return 1

    def _apply_subplots(
        self, conn: sqlite3.Connection, deltas: TruthDeltas,
        hook_id_map: dict[int, str],
    ) -> int:
        """hook_id_map currently unused — subplots ship explicit hook_ids
        from the LLM. Reserved for future implicit references."""
        _ = hook_id_map  # silence unused warning; keeps signature stable
        n = 0
        for s in deltas.subplot_updates:
            if s.action == "new" or not s.thread_id:
                # New subplot — or update-without-id, treated as create
                thread_id = s.thread_id or _new_id()
                conn.execute(
                    Q.INSERT_SUBPLOT,
                    (
                        thread_id, self.project_id, s.name, s.note or None,
                        s.status_after.value, deltas.chapter_num,
                        deltas.chapter_num,
                        json.dumps(s.related_hook_ids, ensure_ascii=False),
                        "[]",
                    ),
                )
            else:
                conn.execute(
                    Q.UPDATE_SUBPLOT,
                    (
                        s.status_after.value, deltas.chapter_num,
                        json.dumps(s.related_hook_ids, ensure_ascii=False),
                        s.thread_id,
                    ),
                )
            n += 1
        return n

    def _apply_emotion_arcs(
        self, conn: sqlite3.Connection, deltas: TruthDeltas,
    ) -> int:
        n = 0
        for e in deltas.emotion_arc_entries:
            conn.execute(
                Q.INSERT_EMOTION_ARC,
                (
                    _new_id(), self.project_id, e.character,
                    deltas.chapter_num, e.from_state, e.to_state, e.trigger,
                ),
            )
            n += 1
        return n

    def _apply_relations(
        self, conn: sqlite3.Connection, deltas: TruthDeltas,
    ) -> int:
        n = 0
        for r in deltas.character_relation_updates:
            conn.execute(
                Q.UPSERT_RELATION,
                (
                    _new_id(), self.project_id, r.character_a, r.character_b,
                    r.relation_type, r.sentiment_score, r.trust_level,
                    deltas.chapter_num, r.note,
                ),
            )
            n += 1
        return n

    # ═══════════ pressure recomputation ═══════════

    def _recompute_pressure(
        self, conn: sqlite3.Connection, current_chapter: int,
    ) -> int:
        """Scan non-terminal hooks and flip to 'pressured' if overdue.
        Returns count of transitions."""
        transitions = 0
        rows = conn.execute(
            Q.SCAN_FOR_PRESSURE, (self.project_id,),
        ).fetchall()
        for hook_id, status, importance, last_advance, threshold in rows:
            effective_threshold = (
                self._pressure_threshold(importance)
                if threshold is None
                else threshold
            )
            last = last_advance if last_advance is not None else 0
            if current_chapter - last >= effective_threshold:
                conn.execute(
                    Q.UPDATE_HOOK_STATUS,
                    (HookStatus.pressured.value, hook_id),
                )
                transitions += 1
        return transitions
