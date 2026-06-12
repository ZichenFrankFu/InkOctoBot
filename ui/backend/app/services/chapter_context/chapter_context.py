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
    HTTP schema. List fields tolerate ``None`` (GenerateRequest's
    ``skills`` defaults to None) by normalizing to ``[]`` in a
    validator.
    """
    model_config = ConfigDict(extra="ignore")

    project_id: str = ""
    chapter_id: str = ""
    chapter_num: int = 1
    generation_mode: GenerationMode = "fresh"
    characters: Optional[list[str]] = None
    skills: Optional[list[str]] = None
    rag_excludes: Optional[list[str]] = None
    on_stage_entities: Optional[dict] = None
    pov_character: str = ""
    scene_types: Optional[list[str]] = None
    user_special_requirements: str = ""

    def model_post_init(self, __ctx) -> None:
        """Normalize None → empty list/dict for fields where callers
        legitimately pass None (e.g. ``GenerateRequest.skills=None``
        means "no explicit skill list"). ChapterContext.build then
        always sees concrete containers."""
        if self.characters is None:
            self.characters = []
        if self.skills is None:
            self.skills = []
        if self.rag_excludes is None:
            self.rag_excludes = []
        if self.scene_types is None:
            self.scene_types = []
        if self.on_stage_entities is None:
            self.on_stage_entities = {}


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
    # Values are normally pre-rendered strings (see prompt_context.builder),
    # but accessors tolerate None / object-shaped values defensively, so the
    # field is typed permissively here.
    loader_blocks: dict[str, Any] = Field(default_factory=dict)
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

    def _block_content(self, block_id: str) -> str:
        """Safe read of a single loader block.

        ``loader_blocks`` values are pre-rendered strings (see
        ``prompt_context.builder``), so this is mostly a typed ``.get``
        with strip + None tolerance. A fallback render path is kept so
        non-string objects (e.g. a future LoaderPlan-shaped value) don't
        explode the pipeline."""
        val = self.loader_blocks.get(block_id)
        if val is None:
            return ""
        if isinstance(val, str):
            return val.strip()
        try:
            budget = getattr(val, "target", None) or getattr(val, "natural_length", 0)
            return (val.render(budget) or "").strip()
        except Exception:
            return ""

    def world_rules_text(self) -> str:
        """worldbook (core) + reference + skills + inspiration —
        the "what is true about this world" bundle for SceneDirector."""
        parts = [
            self._block_content("worldbook"),
            self._block_content("reference"),
            self._block_content("skills"),
            self._block_content("inspiration"),
        ]
        return "\n\n".join(p for p in parts if p)

    def memory_context_text(self) -> str:
        """reader_memory (core) + project_memory + current_chapter_draft
        (only in continue/rewrite modes)."""
        parts = [
            self._block_content("reader_memory"),
            self._block_content("project_memory"),
        ]
        if self.generation_mode in ("continue", "rewrite_from", "modify_section"):
            parts.append(self._block_content("current_chapter_draft"))
        return "\n\n".join(p for p in parts if p)

    def character_cards_text(self) -> str:
        """character_cards block as-is."""
        return self._block_content("character_cards")

    def style_profile_text(self) -> str:
        """platform_directive (market baseline, prepended) +
        user_special_requirements."""
        parts = [
            self._block_content("platform_directive"),
            self._block_content("user_special_requirements"),
        ]
        return "\n\n".join(p for p in parts if p)

    def user_preferences_text(self) -> str:
        """user_preferences block as-is."""
        return self._block_content("user_preferences")

    def narrative_instructions_text(self) -> str:
        """foreshadowing + subplots — loader-side narrative guidance.
        Truth-bundle ``pressured_hooks`` are handled separately."""
        parts = [
            self._block_content("foreshadowing"),
            self._block_content("subplots"),
        ]
        return "\n\n".join(p for p in parts if p)

    def truth_bundle_text(self) -> str:
        """storyland_state loader + TruthFiles bundle merged.
        truth_bundle (TFI) is authoritative; storyland_state loader is
        supplemental and skipped if already represented."""
        parts: list[str] = []
        bundle = (self.truth_bundle.get("bundle_text") or "").strip() \
            if self.truth_bundle else ""
        if bundle:
            parts.append(bundle)
        sl = self._block_content("storyland_state")
        if sl and sl not in (parts[0] if parts else ""):
            parts.append(sl)
        return "\n\n".join(p for p in parts if p)

    def pressured_hooks_text(self) -> str:
        """Pull pressured hooks from truth_bundle (TFI)."""
        if not self.truth_bundle:
            return ""
        return (self.truth_bundle.get("pressured_hooks_text") or "").strip()

    def _compose_constraints(self) -> str:
        """SceneDirector has no narrative-instructions field, so we
        feed foreshadowing/subplots into constraints."""
        return self.narrative_instructions_text()

    def to_scene_director_kwargs(self) -> dict:
        """Assemble the full kwargs for ``SceneDirector.plan_scenes``.

        ``chapter_outline`` prefers ``user_special_requirements`` (set
        explicitly by the user for this chapter) and falls back to the
        ``chapter_outline`` loader block."""
        return {
            "chapter_outline": (self.user_special_requirements
                                or self._block_content("chapter_outline")),
            "chapter_num": self.chapter_num,
            "memory_context": self.memory_context_text(),
            "world_rules": self.world_rules_text(),
            "character_cards": self.character_cards_text(),
            "constraints": self._compose_constraints(),
        }

    def to_writer_assemble_kwargs(self) -> dict:
        """Assemble context kwargs for ``Writer.assemble_chapter``.

        ``performance_records`` / ``narrator_text`` are runtime-produced
        by the simulator and must be supplied by the caller — they are
        deliberately NOT included here."""
        ni = self.narrative_instructions_text()
        ph = self.pressured_hooks_text()
        if ph:
            ni = "\n\n".join(p for p in (ni, "## 待推进伏笔\n" + ph) if p)
        return {
            "chapter_num": self.chapter_num,
            "narrative_instructions": ni,
            "style_profile": self.style_profile_text(),
            "user_preferences": self.user_preferences_text(),
            "memory_context": self.memory_context_text(),
            "truth_bundle": self.truth_bundle_text(),
        }

    def world_rules_legacy_text(self) -> str:
        """Legacy 6-block fold for callers that still want the old
        single-string ``world_rules`` representation (used as fallback
        when the structured accessors are not adopted yet)."""
        parts: list[str] = []
        for key in (
            "character_cards", "worldbook", "reference",
            "storyland_state", "reader_memory", "skills",
        ):
            v = self._block_content(key)
            if v:
                parts.append(v)
        bundle_text = (self.truth_bundle.get("bundle_text") or "").strip() \
            if self.truth_bundle else ""
        if bundle_text:
            parts.append(bundle_text)
        pressured = self.pressured_hooks_text()
        if pressured:
            parts.append(
                "## 待推进伏笔（本章应继续推进）\n" + pressured
            )
        return "\n\n".join(parts).strip()
