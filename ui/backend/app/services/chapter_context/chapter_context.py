"""ChapterContext implementation — see package docstring for purpose."""
from __future__ import annotations

import logging
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


logger = logging.getLogger("inkoctobot.services.chapter_context")


GenerationMode = Literal["fresh", "continue", "rewrite_from", "modify_section"]


class CommitBlocked(RuntimeError):
    """Raised by :meth:`ChapterContext.commit` when an audit gate
    refuses to let the chapter be persisted."""

    def __init__(self, audit_status: str, issues: list[dict]) -> None:
        self.audit_status = audit_status
        self.issues = issues
        super().__init__(
            f"commit blocked by audit gate (status={audit_status}, "
            f"{len(issues)} issues)"
        )


class BuildRequest(BaseModel):
    """Minimal fields ChapterContext.build needs.

    Mirrors a subset of GenerateRequest so existing /start +
    /quick-generate handlers can ``BuildRequest(**req.model_dump())``
    pick what they need without coupling ChapterContext to the full
    HTTP schema.
    """
    model_config = ConfigDict(extra="ignore")

    project_id: str = ""
    chapter_id: str = ""
    chapter_num: int = 1
    generation_mode: GenerationMode = "fresh"
    characters: list[str] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    rag_excludes: list[str] = Field(default_factory=list)
    on_stage_entities: dict = Field(default_factory=dict)
    pov_character: str = ""
    scene_types: list[str] = Field(default_factory=list)
    user_special_requirements: str = ""


class ChapterContext(BaseModel):
    """The "chapter being generated" object — passed through the
    pipeline so every agent sees the same RAG + truth state."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    # ── identifiers ──
    project_id: str
    chapter_id: str
    chapter_num: int = 1
    generation_mode: GenerationMode = "fresh"

    # ── 14 loader output ──
    loader_blocks: dict[str, str] = Field(default_factory=dict)
    sections: list[dict] = Field(default_factory=list)
    diagnostics: dict = Field(default_factory=dict)

    # ── truth files bundle ──
    # Stored as a dict to avoid a hard pydantic ↔ dataclass mismatch —
    # the source TruthBundle dataclass keys are: bundle_text /
    # pressured_hooks_text / ledger_anchors / chapter_id / chapter_num /
    # project_id. ``truth_bundle_obj`` keeps the original instance
    # accessible for typed callers that want it.
    truth_bundle: dict = Field(default_factory=dict)
    truth_bundle_obj: Any = None

    # ── user inputs ──
    characters: list[str] = Field(default_factory=list)
    on_stage_entities: dict = Field(default_factory=dict)
    pov_character: str = ""
    scene_types: list[str] = Field(default_factory=list)
    user_special_requirements: str = ""

    # ── infra ──
    db_path: str = ""

    # ───────────────────── build() ─────────────────────

    @classmethod
    async def build(cls, req: "BuildRequest | dict") -> "ChapterContext":
        """Assemble a ChapterContext from a request dict / pydantic obj.

        Two side-effect-free calls happen here:
          1. build_generation_context — the 14-loader pipeline
          2. TruthFilesIntegration.build_bundle — truth files snapshot

        Either may degrade silently (returning empty blocks / empty
        bundle) on legacy projects without breaking the build.
        """
        if isinstance(req, dict):
            req = BuildRequest(**req)

        db_path = ""
        try:
            from ui.backend.app.services.project_paths import get_db_path
            db_path = get_db_path()
        except Exception:
            pass

        # 1. RAG via the prompt-context builder (14 loaders).
        loader_blocks: dict[str, str] = {}
        sections: list[dict] = []
        diagnostics: dict = {}
        try:
            from ui.backend.app.services.prompt_context.builder import (
                build_generation_context,
            )
            ctx = build_generation_context(
                project_id=req.project_id,
                chapter_num=req.chapter_num,
                characters=req.characters,
                db_path=db_path,
                skills=req.skills,
                chapter_id=req.chapter_id,
                rag_excludes=req.rag_excludes,
            )
            loader_blocks = ctx.get("blocks", {}) or {}
            sections = ctx.get("sections", []) or []
            diagnostics = ctx.get("diagnostics", {}) or {}
        except Exception as e:
            logger.debug("RAG build skipped for %s: %s", req.chapter_id, e)

        # 2. Truth bundle.
        truth_bundle_obj = None
        truth_bundle_dict: dict = {}
        try:
            from ui.backend.app.services.truth_files import TruthFilesIntegration
            truth_bundle_obj = TruthFilesIntegration.build_bundle(
                req.chapter_id,
                chapter_num=req.chapter_num,
                db_path=db_path,
                project_id=req.project_id,
                characters=req.characters,
                pov_character=req.pov_character or None,
            )
            truth_bundle_dict = {
                "bundle_text":          truth_bundle_obj.bundle_text,
                "pressured_hooks_text": truth_bundle_obj.pressured_hooks_text,
                "ledger_anchors":       truth_bundle_obj.ledger_anchors,
                "chapter_id":           truth_bundle_obj.chapter_id,
                "chapter_num":          truth_bundle_obj.chapter_num,
                "project_id":           truth_bundle_obj.project_id,
            }
        except Exception as e:
            logger.debug("truth bundle build skipped for %s: %s",
                          req.chapter_id, e)

        return cls(
            project_id=req.project_id,
            chapter_id=req.chapter_id,
            chapter_num=req.chapter_num,
            generation_mode=req.generation_mode,
            loader_blocks=loader_blocks,
            sections=sections,
            diagnostics=diagnostics,
            truth_bundle=truth_bundle_dict,
            truth_bundle_obj=truth_bundle_obj,
            characters=list(req.characters),
            on_stage_entities=req.on_stage_entities or {},
            pov_character=req.pov_character,
            scene_types=list(req.scene_types),
            user_special_requirements=req.user_special_requirements,
            db_path=db_path,
        )

    # ───────────────────── commit() ─────────────────────

    async def commit(
        self,
        content: str,
        *,
        audit_result: Any = None,
        source: str = "ai",
        model_used: str = "",
        trigger_post_commit: bool = True,
        require_audit_passed: bool = False,
    ) -> str:
        """Persist the chapter text + (optionally) fire the post-commit pipeline.

        Single canonical commit path for both single-agent and
        multi-agent pipelines. Replaces the ad-hoc
        ``_persist_and_trigger_post_commit`` helper in generation_api.

        - ``audit_result`` is the AuditResult from
          ``TruthFilesIntegration.apply_settlement``; when supplied,
          its ``audit_status`` is recorded in ``model_used`` (suffix
          ``.eval_passed`` / ``.eval_failed`` / ``.audit_failed``) so
          downstream loaders can distinguish good from suspect rows.
        - ``require_audit_passed=True`` makes the call raise
          :class:`CommitBlocked` instead of writing when the audit
          failed — multi-agent finalize uses this to enforce the gate.
        - ``trigger_post_commit=False`` skips the fire-and-forget so
          single-agent /quick-generate preview paths don't pollute
          commit_tasks.

        Returns the version_id of the inserted text_versions row, or
        empty string when project_id / chapter_id are missing (the
        "ephemeral preview" path).
        """
        if not self.project_id or not self.chapter_id:
            logger.info("commit skipped — no project/chapter id")
            return ""
        if not self.db_path:
            try:
                from ui.backend.app.services.project_paths import get_db_path
                self.db_path = get_db_path()
            except Exception:
                logger.warning("commit failed — no db_path")
                return ""

        # ── audit gate ──
        audit_status = ""
        audit_issues: list[dict] = []
        if audit_result is not None:
            audit_status = getattr(audit_result, "audit_status", "") or ""
            audit_issues = getattr(audit_result, "issues", []) or []
            if require_audit_passed and audit_status == "audit_failed":
                raise CommitBlocked(audit_status, audit_issues)

        # ── model_used signal ──
        if not model_used:
            if audit_status == "audit_failed":
                model_used = "chapter_context.audit_failed"
            elif audit_status == "audit_passed":
                model_used = "chapter_context.audit_passed"
            elif audit_status:
                model_used = f"chapter_context.{audit_status}"
            else:
                model_used = "chapter_context"

        # ── write text_versions ──
        version_id = ""
        try:
            from ui.backend.app.services import project_store
            project_store.ensure_project_row(self.db_path, self.project_id)
            saved = project_store.insert_version(
                self.db_path, self.project_id,
                {
                    "chapter_id": self.chapter_id,
                    "source": source,
                    "content": content,
                    "model_used": model_used,
                },
            )
            version_id = saved.get("version_id") or ""
        except Exception as e:
            logger.error("commit insert_version failed: %s", e, exc_info=True)
            return ""

        # ── post-commit ──
        if trigger_post_commit:
            try:
                from ui.backend.app.services.commit_pipeline import (
                    fire_and_forget_from_handler,
                )
                fire_and_forget_from_handler(
                    project_id=self.project_id,
                    chapter_id=self.chapter_id,
                    db_path=self.db_path,
                    chapter_text=content,
                    chapter_num=self.chapter_num,
                )
            except Exception as e:
                logger.error("post-commit fire-and-forget failed: %s",
                              e, exc_info=True)

        return version_id

    # ───────────────────── helpers ─────────────────────

    def world_rules_text(self) -> str:
        """Concatenate the loader blocks + truth bundle for legacy
        ``world_rules`` string consumers (multi-agent /start
        currently injects this into req_data['world_rules'])."""
        parts: list[str] = []
        for key in (
            "character_cards", "worldbook", "reference",
            "storyland_state", "reader_memory", "skills",
        ):
            v = (self.loader_blocks.get(key) or "").strip()
            if v:
                parts.append(v)
        bundle_text = (self.truth_bundle.get("bundle_text") or "").strip()
        if bundle_text:
            parts.append(bundle_text)
        pressured = (self.truth_bundle.get("pressured_hooks_text") or "").strip()
        if pressured:
            parts.append(
                "## 待推进伏笔（本章应继续推进）\n" + pressured
            )
        return "\n\n".join(parts).strip()
