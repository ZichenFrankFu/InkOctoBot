"""Canonical Truth Files entry point — used by ChapterContext +
multi-agent /start.

Three operations, all keyed on ``chapter_id`` (the new canonical
identifier; project_id / chapter_num are resolved internally from the
chapters table):

- :meth:`TruthFilesIntegration.build_bundle` — pulls the 7-file
  Markdown bundle + pressured-hook reminder + ledger anchors. Used in
  Phase 1 of the chapter-generation pipeline (injected into Writer's
  memory_context).
- :meth:`TruthFilesIntegration.apply_settlement` — runs the
  settlement LLM call + applies StorylandStateDeltas + writes
  ``chapters.audit_status``. Used in Phase 2 (after Writer produces
  prose).
- :meth:`TruthFilesIntegration.check_audit_gate` — boolean gate
  consumed by finalize — returns False when ``audit_status =
  'audit_failed'`` and the user hasn't explicitly overridden.

The class delegates to the existing helpers in
``agents/production/storyland_state_integration.py`` so legacy
callers (single-writer endpoint) keep working unchanged; this layer
just packages the helpers into a uniform interface keyed on
``chapter_id``.
"""
from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass, field
from typing import Any


logger = logging.getLogger("inkoctobot.services.truth_files.integration")


@dataclass
class TruthBundle:
    """Read-side payload for Phase 1 — what Writer needs to see."""

    chapter_id: str
    chapter_num: int
    project_id: str
    bundle_text: str = ""              # the 7-file markdown blob
    pressured_hooks_text: str = ""     # one-line reminder for narrative
    ledger_anchors: list[dict] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not (self.bundle_text or self.pressured_hooks_text or self.ledger_anchors)


@dataclass
class AuditResult:
    """Write-side result of Phase 2 — what finalize needs to check.

    Mirrors the dict that ``extract_and_apply_state_deltas`` historically
    returned, but exposes it as a typed object so callers don't dig into
    untyped keys.
    """

    chapter_id: str
    chapter_num: int
    success: bool
    audit_status: str                  # 'audit_passed' / 'audit_failed' / 'audit_pending'
    issues: list[dict] = field(default_factory=list)
    cross_ref_issues: list[dict] = field(default_factory=list)
    applied_counts: dict = field(default_factory=dict)
    has_errors: bool = False
    deltas_hash: str = ""
    idempotent_hit: bool = False
    error: str | None = None

    @property
    def passed(self) -> bool:
        return self.audit_status == "audit_passed"

    @property
    def failed(self) -> bool:
        return self.audit_status == "audit_failed"


class TruthFilesIntegration:
    """Stateless façade over the storyland_state_integration helpers.

    All methods are classmethods — there's no per-instance state to
    track; the underlying ``StorylandStateStore`` is created per call
    by the helpers (which already cache its connection internally).
    """

    # ────────────── Phase 1: build_bundle ──────────────

    @classmethod
    def build_bundle(
        cls,
        chapter_id: str,
        chapter_num: int | None = None,
        *,
        db_path: str | None = None,
        project_id: str | None = None,
        characters: list[str] | None = None,
        pov_character: str | None = None,
        budgets_per_kind: int | None = None,
    ) -> TruthBundle:
        """Build the read-side Truth bundle for a chapter.

        ``chapter_id`` is the canonical key. ``chapter_num`` and
        ``project_id`` are resolved from the chapters table when not
        supplied — but passing them in directly avoids the lookup
        round-trip and lets the caller pre-cache values.
        """
        resolved_db, resolved_project, resolved_num = cls._resolve_keys(
            chapter_id, db_path=db_path,
            project_id=project_id, chapter_num=chapter_num,
        )
        if not resolved_project or resolved_num is None:
            return TruthBundle(
                chapter_id=chapter_id, chapter_num=resolved_num or 0,
                project_id=resolved_project or "",
            )

        from agents.production import storyland_state_integration as ssi

        try:
            bundle_text = ssi.build_state_context(
                resolved_project, resolved_db,
                chapter_num=resolved_num,
                characters=characters or [],
                budgets_per_kind=budgets_per_kind,
                pov_character=pov_character,
            ) or ""
        except Exception as e:
            logger.debug("build_state_context failed for %s: %s", chapter_id, e)
            bundle_text = ""

        try:
            pressured = ssi.list_pressured_hooks_text(
                resolved_project, resolved_db, current_chapter=resolved_num,
            ) or ""
        except Exception as e:
            logger.debug("pressured_hooks failed for %s: %s", chapter_id, e)
            pressured = ""

        try:
            anchors = ssi.build_ledger_anchors(
                resolved_project, resolved_db, chapter_num=resolved_num,
                characters=characters or [],
            ) or []
        except AttributeError:
            # build_ledger_anchors may not exist on every branch.
            anchors = []
        except Exception as e:
            logger.debug("build_ledger_anchors failed for %s: %s", chapter_id, e)
            anchors = []

        return TruthBundle(
            chapter_id=chapter_id, chapter_num=resolved_num,
            project_id=resolved_project,
            bundle_text=bundle_text,
            pressured_hooks_text=pressured,
            ledger_anchors=anchors,
        )

    # ────────────── Phase 2: apply_settlement ──────────────

    @classmethod
    async def apply_settlement(
        cls,
        chapter_id: str,
        content: str,
        *,
        router: Any = None,
        db_path: str | None = None,
        project_id: str | None = None,
        chapter_num: int | None = None,
        chapter_title: str = "",
        pov_character: str = "",
        known_characters: set[str] | None = None,
        agent_role: str = "consolidator",
        persist_audit_status: bool = True,
    ) -> AuditResult:
        """Settlement: extract state deltas from ``content`` + apply
        to truth tables + persist ``chapters.audit_status``.

        When ``router`` is None the helper constructs a default
        ModelRouter — fine for production but tests should always
        inject a mock to avoid network calls.
        """
        resolved_db, resolved_project, resolved_num = cls._resolve_keys(
            chapter_id, db_path=db_path,
            project_id=project_id, chapter_num=chapter_num,
        )
        if not resolved_project or resolved_num is None:
            return AuditResult(
                chapter_id=chapter_id, chapter_num=resolved_num or 0,
                success=False, audit_status="audit_pending",
                error="missing project_id or chapter_num — cannot run settlement",
            )

        if router is None:
            try:
                from llm.router import ModelRouter
                router = ModelRouter()
            except Exception as e:
                logger.warning("ModelRouter init failed: %s", e)
                return AuditResult(
                    chapter_id=chapter_id, chapter_num=resolved_num,
                    success=False, audit_status="audit_pending",
                    error=f"router unavailable: {e}",
                )

        from agents.production import storyland_state_integration as ssi

        # Optional: feed ledger_anchors so the settlement prompt
        # surfaces "must match old_value" preconditions.
        anchors = cls.build_bundle(
            chapter_id, chapter_num=resolved_num,
            db_path=resolved_db, project_id=resolved_project,
        ).ledger_anchors

        try:
            raw = await ssi.extract_and_apply_state_deltas(
                router, content,
                project_id=resolved_project, db_path=resolved_db,
                chapter_num=resolved_num, chapter_title=chapter_title,
                pov_character=pov_character,
                known_characters=known_characters,
                agent_role=agent_role,
                ledger_anchors=anchors,
            )
        except Exception as e:
            logger.exception("apply_settlement failed for %s", chapter_id)
            return AuditResult(
                chapter_id=chapter_id, chapter_num=resolved_num,
                success=False, audit_status="audit_pending",
                error=str(e)[:300],
            )

        result = AuditResult(
            chapter_id=chapter_id, chapter_num=resolved_num,
            success=bool(raw.get("success")),
            audit_status=str(raw.get("audit_status") or "audit_pending"),
            issues=raw.get("issues") or [],
            cross_ref_issues=raw.get("cross_ref_issues") or [],
            applied_counts=raw.get("applied_counts") or {},
            has_errors=bool(raw.get("has_errors")),
            deltas_hash=str(raw.get("deltas_hash") or ""),
            idempotent_hit=bool(raw.get("idempotent_hit")),
            error=raw.get("error"),
        )

        if persist_audit_status:
            cls._persist_audit_status(
                resolved_db, resolved_project, resolved_num, result,
            )

        return result

    # ────────────── audit gate ──────────────

    @classmethod
    def check_audit_gate(
        cls,
        chapter_id: str,
        *,
        db_path: str | None = None,
        project_id: str | None = None,
        chapter_num: int | None = None,
    ) -> tuple[bool, str]:
        """Hard gate consumed by finalize. Returns ``(allowed, reason)``.

        ``allowed = False`` when ``chapters.audit_status = 'audit_failed'``
        without an explicit user override. Mirrors
        ``project_store.can_finalize_chapter`` but keyed on
        ``chapter_id`` instead of ``(project_id, chapter_num)``.
        """
        resolved_db, resolved_project, resolved_num = cls._resolve_keys(
            chapter_id, db_path=db_path,
            project_id=project_id, chapter_num=chapter_num,
        )
        if not resolved_project or resolved_num is None:
            # Missing identifiers → treat as ungated (no chapter to gate).
            return True, ""
        try:
            from ui.backend.app.services import project_store
            return project_store.can_finalize_chapter(
                resolved_db, resolved_project, resolved_num,
            )
        except Exception as e:
            logger.warning("check_audit_gate failed for %s: %s", chapter_id, e)
            # Fail open — don't block on infra errors.
            return True, ""

    # ────────────── helpers ──────────────

    @staticmethod
    def _resolve_keys(
        chapter_id: str,
        *,
        db_path: str | None,
        project_id: str | None,
        chapter_num: int | None,
    ) -> tuple[str, str, int | None]:
        """Resolve db_path / project_id / chapter_num from chapter_id
        when the caller didn't supply them."""
        if not db_path:
            try:
                from ui.backend.app.services.project_paths import get_db_path
                db_path = get_db_path()
            except Exception:
                db_path = ""

        if project_id and chapter_num is not None:
            return db_path, project_id, int(chapter_num)

        if not db_path or not chapter_id:
            return db_path or "", project_id or "", chapter_num

        try:
            with sqlite3.connect(db_path) as con:
                row = con.execute(
                    "SELECT project_id, chapter_num FROM chapters "
                    "WHERE chapter_id = ?",
                    (chapter_id,),
                ).fetchone()
            if not row:
                return db_path, project_id or "", chapter_num
            return (
                db_path,
                project_id or str(row[0] or ""),
                chapter_num if chapter_num is not None else int(row[1] or 0),
            )
        except Exception as e:
            logger.debug("resolve_keys failed for %s: %s", chapter_id, e)
            return db_path, project_id or "", chapter_num

    @staticmethod
    def _persist_audit_status(
        db_path: str, project_id: str, chapter_num: int,
        result: AuditResult,
    ) -> None:
        try:
            from ui.backend.app.services import project_store
            project_store.update_chapter_audit(
                db_path, project_id, chapter_num=chapter_num,
                audit_status=result.audit_status,
                audit_issues=result.issues,
            )
        except Exception as e:
            logger.warning(
                "persist audit_status failed for %s/ch%d: %s",
                project_id, chapter_num, e,
            )
