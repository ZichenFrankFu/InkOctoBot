"""Unified entry point for every LLM call in the codebase.

Every module that previously did::

    from llm.fallback_router import get_fallback_router
    fr = get_fallback_router(router)
    raw = await fr.invoke(primary_role="...", prompt=..., system=...)

should switch to::

    from llm.call_site import LLMCallSite
    cs = LLMCallSite(
        call_site_id="post_commit.summarizer",
        primary_role="post_commit",
        parsed_target_table="chapter_summaries",
    )
    raw = await cs.invoke(
        prompt=..., system=...,
        project_id=..., chapter_id=...,
    )

What that single call now does:

1. Resolves provider + model via ModelRouter / settings.
2. Picks the right tokenizer (HF for local, tiktoken for OpenAI, etc.)
   and counts the prompt tokens.
3. Reads ``settings.llm_manual_mode`` — global on/off switch.
4. **Auto mode**: calls FallbackRouter (primary → fallback) and times it.
5. **Manual mode**: registers a paste token via :mod:`llm.manual_paste`,
   pushes a notification to EventBus, awaits the user's paste.
6. Writes one row into ``llm_outputs`` (audit + manual-mode anchor).
7. Returns the raw response string so the caller's existing JSON
   parser keeps working unchanged.
"""
from __future__ import annotations

import hashlib
import logging
import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from typing import Any


logger = logging.getLogger("inkoctobot.llm.call_site")


def _new_output_id() -> str:
    return f"lo_{uuid.uuid4().hex[:12]}"


def _hash_prompt(prompt: str, system: str) -> str:
    h = hashlib.sha256()
    h.update((system or "").encode("utf-8"))
    h.update(b"\x1f")
    h.update((prompt or "").encode("utf-8"))
    return h.hexdigest()[:16]


def _is_manual_mode() -> bool:
    """Read the global toggle from data/settings.json. Falls back to
    auto mode if the file or key is missing (e.g. legacy installs)."""
    try:
        from ui.backend.app.settings import settings as _s
        path = _s.get_data_path("settings.json")
        if not path.exists():
            return False
        import json
        blob = json.loads(path.read_text("utf-8"))
        return bool(blob.get("llm_manual_mode"))
    except Exception:
        return False


@dataclass
class LLMCallSite:
    """One call site = one logical place the codebase asks an LLM
    something. Stateless and cheap to construct per call.

    Fields:
    - ``call_site_id`` — stable identifier, e.g. ``"post_commit.summarizer"``.
      Used to filter the audit view and bind manual-mode pastes.
    - ``primary_role`` — ModelRouter role key (resolved per-call).
    - ``fallback_role`` — optional second role to try on primary failure.
    - ``parsed_target_table`` / ``parsed_target_row_id`` — the row the
      parsed output will land in. Recorded in the audit so the review
      UI can jump from an LLM output to the table row it produced.
    - ``default_max_tokens`` / ``default_temperature`` — sensible
      defaults; per-call kwargs override.
    """

    call_site_id: str
    primary_role: str = "post_commit"
    fallback_role: str | None = None
    parsed_target_table: str = ""
    default_max_tokens: int = 2000
    default_temperature: float = 0.3

    # ─────────── public ───────────

    async def invoke(
        self,
        *,
        prompt: str,
        system: str = "",
        max_tokens: int | None = None,
        temperature: float | None = None,
        project_id: str = "",
        chapter_id: str = "",
        parsed_target_row_id: str = "",
        db_path: str | None = None,
        manual_mode: bool | None = None,
    ) -> str:
        """Return the raw LLM response (auto or manual). Always writes
        an ``llm_outputs`` audit row before returning. ``manual_mode``
        overrides the global toggle when provided (mostly for tests).
        """
        if max_tokens is None:
            max_tokens = self.default_max_tokens
        if temperature is None:
            temperature = self.default_temperature
        if manual_mode is None:
            manual_mode = _is_manual_mode()

        # Resolve provider/model for audit + tokenization.
        provider, model = self._resolve_provider_model()
        prompt_tokens = self._count_tokens(provider, model, prompt + system)

        t0 = time.perf_counter()
        if manual_mode:
            raw = await self._invoke_manual(
                prompt, system,
                project_id=project_id, chapter_id=chapter_id,
            )
            source = "manual"
        else:
            raw = await self._invoke_auto(
                prompt, system,
                max_tokens=max_tokens, temperature=temperature,
            )
            source = "auto"
        duration_ms = (time.perf_counter() - t0) * 1000.0

        response_tokens = self._count_tokens(provider, model, raw)

        self._persist_audit(
            db_path=db_path,
            project_id=project_id, chapter_id=chapter_id,
            parsed_target_row_id=parsed_target_row_id,
            prompt=prompt, system=system, raw=raw,
            source=source, provider=provider, model=model,
            prompt_tokens=prompt_tokens, response_tokens=response_tokens,
            duration_ms=duration_ms,
        )
        return raw

    def dry_run(self, prompt: str, system: str = "") -> dict[str, Any]:
        """Don't call any LLM — just return the prompt + sizing info.
        Useful for the preview pane and unit tests that don't want to
        wire the manual-paste machinery."""
        provider, model = self._resolve_provider_model()
        return {
            "call_site_id":   self.call_site_id,
            "provider":       provider,
            "model":          model,
            "prompt":         prompt,
            "system":         system,
            "prompt_tokens":  self._count_tokens(provider, model, prompt + system),
            "prompt_hash":    _hash_prompt(prompt, system),
        }

    # ─────────── internals ───────────

    def _resolve_provider_model(self) -> tuple[str, str]:
        try:
            from llm.router import ModelRouter
            return ModelRouter().resolve_role(self.primary_role)
        except Exception as e:
            logger.debug("provider resolve failed: %s", e)
            return ("", "")

    @staticmethod
    def _count_tokens(provider: str, model: str, text: str) -> int:
        try:
            from llm.tokenizer_registry import count_tokens
            return count_tokens(provider, model, text or "")
        except Exception:
            return 0

    async def _invoke_auto(
        self, prompt: str, system: str,
        *, max_tokens: int, temperature: float,
    ) -> str:
        from llm.fallback_router import get_fallback_router
        from llm.router import ModelRouter
        router = ModelRouter()
        fr = get_fallback_router(router)
        return await fr.invoke(
            primary_role=self.primary_role,
            fallback_role=self.fallback_role,
            prompt=prompt, system=system,
            max_tokens=max_tokens, temperature=temperature,
        )

    async def _invoke_manual(
        self, prompt: str, system: str,
        *, project_id: str, chapter_id: str,
    ) -> str:
        from llm.manual_paste import await_paste, register_pending_paste
        token = register_pending_paste(
            self.call_site_id, prompt, system,
            project_id=project_id, chapter_id=chapter_id,
        )
        logger.info(
            "manual paste registered: site=%s token=%s",
            self.call_site_id, token,
        )
        return await await_paste(token)

    def _persist_audit(
        self, *, db_path: str | None,
        project_id: str, chapter_id: str, parsed_target_row_id: str,
        prompt: str, system: str, raw: str,
        source: str, provider: str, model: str,
        prompt_tokens: int, response_tokens: int, duration_ms: float,
    ) -> None:
        """Best-effort write to llm_outputs. Failure logs but doesn't
        break the call chain — the audit row is the secondary record."""
        path = db_path
        if path is None:
            try:
                from ui.backend.app.services.project_paths import get_db_path
                path = get_db_path()
            except Exception:
                path = None
        if not path:
            logger.debug("audit skipped — no db_path resolvable")
            return
        try:
            with sqlite3.connect(path) as con:
                con.execute(
                    "INSERT INTO llm_outputs "
                    "(output_id, call_site_id, project_id, chapter_id, "
                    " prompt_hash, prompt_full, system_prompt, response_raw, "
                    " parsed_target_table, parsed_target_row_id, source, "
                    " provider, model, token_count_prompt, "
                    " token_count_response, duration_ms) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (_new_output_id(), self.call_site_id,
                     project_id or None, chapter_id or None,
                     _hash_prompt(prompt, system),
                     prompt, system or None, raw,
                     self.parsed_target_table or None,
                     parsed_target_row_id or None,
                     source, provider or None, model or None,
                     prompt_tokens, response_tokens, duration_ms),
                )
                con.commit()
        except sqlite3.OperationalError as e:
            logger.debug("audit table missing: %s", e)
        except Exception as e:
            logger.warning("llm audit write failed: %s", e)
