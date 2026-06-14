"""StorylandStateStore — the state authority for the Truth File system.

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

from knowledge.storyland_state import sql as Q
from knowledge.storyland_state.schemas import (
    ApplyResult, HookDelta, HookStatus, StorylandStateDeltas, ValidationIssue,
)

logger = logging.getLogger("inkoctobot.knowledge.storyland_state.store")


def _new_id() -> str:
    """12-char uuid hex matching the style used elsewhere in the codebase."""
    return uuid.uuid4().hex[:12]


def _hash_deltas(deltas: StorylandStateDeltas) -> str:
    """Deterministic SHA-256 over StorylandStateDeltas (sorted JSON)."""
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


class StorylandStateStore:
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
        self, deltas: StorylandStateDeltas, *, validate: bool = False,
        allow_backfill: bool = False,
        known_characters: set[str] | None = None,
    ) -> ApplyResult:
        """Atomically apply a StorylandStateDeltas bundle.

        Idempotent: a (project_id, deltas_hash) collision returns the
        cached ApplyResult without re-writing.

        When ``validate=True`` runs ``knowledge.storyland_state.validators.validate_deltas``;
        any error-severity issue aborts the apply. ``known_characters`` is
        passed through; ``allow_backfill=True`` lets the chapter_monotonic
        rule pass for migration tooling.
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

        # ── Step 2: optional pre-validation ──
        cross_ref_issues: list[ValidationIssue] = []
        if validate:
            try:
                from knowledge.storyland_state.validators import validate_deltas as _vd
                cross_ref_issues = _vd(
                    self, deltas,
                    known_characters=known_characters,
                    allow_backfill=allow_backfill,
                )
            except ImportError:
                pass  # validators module not present
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
        self, conn: sqlite3.Connection, deltas: StorylandStateDeltas,
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
                        new_id, self.project_id, p.subject, p.subject_type,
                        p.predicate, p.object, p.valid_from_chapter, 1.0,
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
        self, conn: sqlite3.Connection, deltas: StorylandStateDeltas,
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
        self, conn: sqlite3.Connection, deltas: StorylandStateDeltas,
    ) -> dict[int, str]:
        """Apply hook deltas. Returns map {delta_index → resolved hook_id}
        so subsequent subplots can reference newly-created hooks."""
        resolved: dict[int, str] = {}
        for idx, h in enumerate(deltas.hook_deltas):
            hook_id = h.hook_id
            if h.action == "new":
                hook_id = hook_id or _new_id()
                threshold = self._pressure_threshold(h.importance.value)
                # 机制4: 预期回收章节默认 = 埋设章 + 规模窗口；用户/LLM
                # 显式给出的 expected_payoff_chapter 优先；世界真相无窗口。
                from .schemas import HOOK_SCALE_WINDOWS
                scale = getattr(h, "scale", None)
                scale_value = scale.value if scale is not None else "event_clue"
                expected = h.expected_payoff_chapter
                if expected is None:
                    window = HOOK_SCALE_WINDOWS.get(scale_value)
                    if window is not None:
                        expected = deltas.chapter_num + window
                import json as _json
                conn.execute(
                    Q.INSERT_HOOK,
                    (
                        hook_id, self.project_id, h.description, "open",
                        h.importance.value, scale_value, deltas.chapter_num,
                        expected, deltas.chapter_num,
                        deltas.chapter_num, threshold,
                        1 if h.is_spoiler else 0,
                        _json.dumps(list(h.revealed_to_chars), ensure_ascii=False),
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
        self, conn: sqlite3.Connection, deltas: StorylandStateDeltas,
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
        self, conn: sqlite3.Connection, deltas: StorylandStateDeltas,
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
        self, conn: sqlite3.Connection, deltas: StorylandStateDeltas,
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
        for (hook_id, status, importance, last_advance, threshold,
             scale, expected_payoff) in rows:
            effective_threshold = (
                self._pressure_threshold(importance)
                if threshold is None
                else threshold
            )
            last = last_advance if last_advance is not None else 0
            stale = current_chapter - last >= effective_threshold
            # 机制4: 超期 = 当前章号超过预期回收章节（世界真相
            # expected_payoff 为 NULL，永不超期）。到达预期章节当章
            # 仅由 loader 标注"应回收"（机制7），不在此翻转状态。
            overdue = (
                expected_payoff is not None
                and scale != "world_truth"
                and current_chapter > expected_payoff
            )
            if stale or overdue:
                conn.execute(
                    Q.UPDATE_HOOK_STATUS,
                    (HookStatus.pressured.value, hook_id),
                )
                transitions += 1
        return transitions

    # ═══════════ C4 — read API ═══════════

    def _rows_as_dicts(
        self, cursor: sqlite3.Cursor,
    ) -> list[dict[str, Any]]:
        cols = [c[0] for c in cursor.description]
        return [dict(zip(cols, row)) for row in cursor.fetchall()]

    def query_current_state(
        self, *, subject: str | None = None, predicate: str | None = None,
        chapter_num: int | None = None,
    ) -> list[dict[str, Any]]:
        """Return SPO triples for this project. If chapter_num given,
        only triples whose validity window covers that chapter."""
        sql = (
            "SELECT triple_id, subject, predicate, object, "
            "valid_from_chapter, valid_to_chapter, superseded_by, confidence "
            "FROM truth_current_state WHERE project_id = ?"
        )
        params: list[Any] = [self.project_id]
        if subject is not None:
            sql += " AND subject = ?"; params.append(subject)
        if predicate is not None:
            sql += " AND predicate = ?"; params.append(predicate)
        if chapter_num is not None:
            sql += (
                " AND valid_from_chapter <= ? AND "
                "(valid_to_chapter IS NULL OR valid_to_chapter >= ?)"
            )
            params.extend([chapter_num, chapter_num])
        sql += " ORDER BY subject, predicate, valid_from_chapter"
        with self._connect() as conn:
            cur = conn.execute(sql, params)
            return self._rows_as_dicts(cur)

    def query_ledger(
        self, character: str, key: str | None = None,
        as_of_chapter: int | None = None,
    ) -> int | None:
        """Latest new_value for (character, key) at or before as_of_chapter.
        None if no entry. ``key`` is required when caller wants a single
        scalar; without it returns None.
        """
        if key is None:
            return None
        sql = (
            "SELECT new_value FROM character_ledger "
            "WHERE project_id = ? AND character_name = ? AND key = ?"
        )
        params: list[Any] = [self.project_id, character, key]
        if as_of_chapter is not None:
            sql += " AND chapter_num <= ?"
            params.append(as_of_chapter)
        sql += " ORDER BY chapter_num DESC, created_at DESC LIMIT 1"
        with self._connect() as conn:
            row = conn.execute(sql, params).fetchone()
        return row[0] if row else None

    def list_ledger_entries(
        self, character: str | None = None, key: str | None = None,
    ) -> list[dict[str, Any]]:
        sql = (
            "SELECT ledger_id, character_name, chapter_num, category, key, "
            "operation, delta, new_value, reason, in_text_evidence, "
            "created_at FROM character_ledger WHERE project_id = ?"
        )
        params: list[Any] = [self.project_id]
        if character is not None:
            sql += " AND character_name = ?"; params.append(character)
        if key is not None:
            sql += " AND key = ?"; params.append(key)
        sql += " ORDER BY chapter_num, created_at"
        with self._connect() as conn:
            cur = conn.execute(sql, params)
            return self._rows_as_dicts(cur)

    def query_pending_hooks(
        self, *, status: str | None = None, importance: str | None = None,
    ) -> list[dict[str, Any]]:
        sql = (
            "SELECT hook_id, description, status, importance, "
            "origin_chapter, expected_payoff_chapter, last_mention_chapter, "
            "last_advance_chapter, pressure_threshold, "
            "is_spoiler, revealed_to_chars_json, "
            "created_at, updated_at "
            "FROM pending_hooks WHERE project_id = ?"
        )
        params: list[Any] = [self.project_id]
        if status is not None:
            sql += " AND status = ?"; params.append(status)
        if importance is not None:
            sql += " AND importance = ?"; params.append(importance)
        sql += " ORDER BY "\
               "  CASE status "\
               "    WHEN 'pressured' THEN 0 "\
               "    WHEN 'near_payoff' THEN 1 "\
               "    WHEN 'progressing' THEN 2 "\
               "    WHEN 'open' THEN 3 "\
               "    WHEN 'resolved' THEN 4 "\
               "    WHEN 'abandoned' THEN 5 "\
               "  END, origin_chapter"
        with self._connect() as conn:
            cur = conn.execute(sql, params)
            return self._rows_as_dicts(cur)

    def list_pressured_hooks(
        self, current_chapter: int,
    ) -> list[dict[str, Any]]:
        """Return hooks that are persistently 'pressured' OR would be
        pressured given current_chapter (open/progressing past threshold)."""
        out: list[dict[str, Any]] = []
        all_hooks = self.query_pending_hooks()
        for h in all_hooks:
            status = h["status"]
            if status == "pressured":
                out.append(h)
                continue
            if status not in ("open", "progressing"):
                continue
            threshold = h.get("pressure_threshold") or \
                self._pressure_threshold(h.get("importance", "B"))
            last = h.get("last_advance_chapter")
            if last is None:
                last = h.get("origin_chapter", 0)
            if current_chapter - last >= threshold:
                out.append(h)
        return out

    def query_chapter_summary(
        self, chapter_num: int,
    ) -> dict[str, Any] | None:
        with self._connect() as conn:
            cur = conn.execute(
                "SELECT summary_id, chapter_num, summary_text, "
                "key_events_json, character_states_json, is_active, created_at "
                "FROM chapter_summaries WHERE project_id = ? AND chapter_num = ?",
                (self.project_id, chapter_num),
            )
            rows = self._rows_as_dicts(cur)
        return rows[0] if rows else None

    def list_chapter_summaries(
        self, *, limit: int | None = None,
    ) -> list[dict[str, Any]]:
        sql = (
            "SELECT summary_id, chapter_num, summary_text, key_events_json, "
            "character_states_json, is_active, created_at "
            "FROM chapter_summaries WHERE project_id = ? AND is_active = 1 "
            "ORDER BY chapter_num DESC"
        )
        params: list[Any] = [self.project_id]
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        with self._connect() as conn:
            cur = conn.execute(sql, params)
            rows = self._rows_as_dicts(cur)
        # Normalize to renderer-friendly keys
        for r in rows:
            r["key_events"] = r.pop("key_events_json", "[]")
        return rows

    def query_subplot_threads(
        self, *, status: str | None = None,
    ) -> list[dict[str, Any]]:
        sql = (
            "SELECT thread_id, name, description, status, start_chapter, "
            "last_advanced_chapter, related_hook_ids_json, "
            "related_character_ids_json, created_at, updated_at "
            "FROM subplot_threads WHERE project_id = ?"
        )
        params: list[Any] = [self.project_id]
        if status is not None:
            sql += " AND status = ?"; params.append(status)
        sql += " ORDER BY start_chapter"
        with self._connect() as conn:
            cur = conn.execute(sql, params)
            rows = self._rows_as_dicts(cur)
        for r in rows:
            r["related_hook_ids"] = r.pop("related_hook_ids_json", "[]")
        return rows

    def query_emotion_arc(
        self, character: str | None = None, *,
        since_chapter: int | None = None,
    ) -> list[dict[str, Any]]:
        sql = (
            "SELECT arc_id, character_name, chapter_num, from_state, "
            "to_state, trigger, created_at FROM emotion_arcs "
            "WHERE project_id = ?"
        )
        params: list[Any] = [self.project_id]
        if character is not None:
            sql += " AND character_name = ?"; params.append(character)
        if since_chapter is not None:
            sql += " AND chapter_num >= ?"; params.append(since_chapter)
        sql += " ORDER BY character_name, chapter_num"
        with self._connect() as conn:
            cur = conn.execute(sql, params)
            return self._rows_as_dicts(cur)

    # ═══════════ C4 — Markdown rendering ═══════════

    def render_for_prompt(
        self, kind, *, chapter_num: int,
        characters: list[str] | None = None,
        budget_tokens: int = 2000,
        pov_character: str | None = None,
    ) -> str:
        """Render one truth file as Markdown for LLM context.

        ``budget_tokens`` is converted to char budget at 2× ratio
        (roughly accurate for Chinese; chars under-counted for English).

        A1 InkOS spoiler filter: ``pov_character`` (when set) filters
        ``pending_hooks`` to omit hooks with ``is_spoiler=1`` unless that
        character is in the hook's ``revealed_to_chars`` list. Pass
        ``None`` for omniscient narrator / writer-pipeline view.
        """
        from knowledge.storyland_state.markdown_renderer import render as _md_render
        budget_chars = max(budget_tokens * 2, 200)
        rows = self._rows_for_kind(kind, chapter_num=chapter_num,
                                    characters=characters)
        from knowledge.storyland_state.schemas import StorylandStateKind as _Kind
        if kind == _Kind.pending_hooks and pov_character is not None:
            # Renderer-level filter (the only kind that takes pov_character)
            from knowledge.storyland_state.markdown_renderer import render_pending_hooks
            return render_pending_hooks(
                rows, project_id=self.project_id,
                chapter_pointer=chapter_num, budget_chars=budget_chars,
                pov_character=pov_character,
            )
        return _md_render(
            kind, rows, project_id=self.project_id,
            chapter_pointer=chapter_num, budget_chars=budget_chars,
        )

    def render_bundle_for_prompt(
        self, *, chapter_num: int,
        characters: list[str] | None = None,
        kinds: list | None = None,
        budgets: dict | None = None,
        pov_character: str | None = None,
    ) -> dict:
        """Render multiple truth files in one call.

        A1: pass ``pov_character`` to apply the spoiler filter to
        pending_hooks (and any future spoiler-aware truth files).
        """
        from knowledge.storyland_state.schemas import StorylandStateKind as _Kind
        kinds = kinds if kinds is not None else list(_Kind)
        budgets = budgets or {}
        out: dict = {}
        for k in kinds:
            budget = budgets.get(k) or self._default_budget_tokens(k)
            out[k] = self.render_for_prompt(
                k, chapter_num=chapter_num,
                characters=characters, budget_tokens=budget,
                pov_character=pov_character,
            )
        return out

    def export_markdown(
        self, output_dir: str | None = None, *,
        kinds: list | None = None,
        chapter_pointer: int | None = None,
    ) -> dict:
        """Export each truth file as a .md file under ``output_dir``.

        Default output_dir = data/truth_files/{project_id}/.
        Returns {kind: Path}.
        """
        from pathlib import Path as _Path
        from knowledge.storyland_state.schemas import StorylandStateKind as _Kind
        kinds = kinds if kinds is not None else list(_Kind)
        if output_dir is None:
            try:
                import config as _cfg
                root = _Path(_cfg.BASE_DIR) / "data" / "truth_files"
            except Exception:
                root = _Path("data") / "truth_files"
            output_dir = root / self.project_id
        out_path = _Path(output_dir)
        out_path.mkdir(parents=True, exist_ok=True)

        if chapter_pointer is None:
            with self._connect() as conn:
                row = conn.execute(
                    Q.LATEST_APPLIED_CHAPTER, (self.project_id,),
                ).fetchone()
            chapter_pointer = (row[0] if row and row[0] is not None else 0)

        results: dict = {}
        for k in kinds:
            md = self.render_for_prompt(
                k, chapter_num=chapter_pointer,
                budget_tokens=10_000,  # generous for file export
            )
            path = out_path / f"{k.value}.md"
            path.write_text(md, encoding="utf-8")
            results[k] = path
        return results

    # ───── internals ─────

    def _rows_for_kind(
        self, kind, *, chapter_num: int,
        characters: list[str] | None,
    ) -> list[dict[str, Any]]:
        from knowledge.storyland_state.schemas import StorylandStateKind as _Kind
        if kind == _Kind.current_state:
            rows = self.query_current_state()
            if characters:
                rows = [r for r in rows if r["subject"] in characters]
            return rows
        if kind == _Kind.particle_ledger:
            rows = self.list_ledger_entries()
            if characters:
                rows = [r for r in rows if r["character_name"] in characters]
            return rows
        if kind == _Kind.pending_hooks:
            return self.query_pending_hooks()
        if kind == _Kind.chapter_summaries:
            return self.list_chapter_summaries(limit=10)
        if kind == _Kind.subplot_board:
            return self.query_subplot_threads()
        if kind == _Kind.emotional_arcs:
            rows = self.query_emotion_arc()
            if characters:
                rows = [r for r in rows if r["character_name"] in characters]
            return rows
        return []

    def _default_budget_tokens(self, kind) -> int:
        budgets = (self._config.get("render_budgets") or {})
        return int(budgets.get(kind.value, 2000))
