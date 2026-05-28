"""Fallback router — wraps :class:`ModelRouter` with a primary/fallback
chain so post-commit tasks can prefer a local model and quietly fall
back to cloud when the local endpoint is down.

Usage::

    router = get_fallback_router()
    response = await router.invoke(
        primary_role="post_commit",
        fallback_role="post_commit_fallback",
        prompt="...",
    )

The fallback role defaults to ``<primary>_fallback`` if not given. If
the fallback also fails, :class:`LLMFallbackExhausted` is raised so
the caller (typically a commit_pipeline sub-task) can catch it once
and create a user notification.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger("inkoctobot.llm.fallback")


@dataclass
class FallbackAttempt:
    role: str
    provider: str
    model: str
    error: str = ""


class LLMFallbackExhausted(RuntimeError):
    """Raised when every model in the fallback chain has failed.

    ``attempts`` carries the per-step diagnostics so the caller can
    surface them to the user in a notification.
    """

    def __init__(self, attempts: list[FallbackAttempt]) -> None:
        self.attempts = attempts
        summary = " → ".join(
            f"{a.role}({a.provider}/{a.model}): {a.error[:80]}"
            for a in attempts
        )
        super().__init__(f"LLM fallback exhausted: {summary}")


class FallbackRouter:
    """Thin wrapper around an existing :class:`ModelRouter`.

    Stateless w.r.t. the underlying router — multiple FallbackRouter
    instances over the same ModelRouter are fine."""

    def __init__(self, model_router) -> None:
        self._mr = model_router

    async def invoke(
        self,
        *,
        primary_role: str,
        fallback_role: str | None = None,
        prompt: str,
        system: str | None = None,
        max_tokens: int = 2000,
        temperature: float = 0.3,
        **kwargs: Any,
    ) -> str:
        """Try ``primary_role`` first; on any exception try ``fallback_role``.

        ``fallback_role`` defaults to ``<primary_role>_fallback``. If
        the fallback role isn't bound the chain stops at the primary.
        """
        roles = [primary_role]
        fb = fallback_role or f"{primary_role}_fallback"
        # Only add fallback if it's actually a different binding —
        # otherwise the second attempt would just hit the same model.
        primary_binding = self._mr.resolve_role(primary_role)
        fb_binding = self._mr.resolve_role(fb)
        if fb_binding != ("", "") and fb_binding != primary_binding:
            roles.append(fb)

        attempts: list[FallbackAttempt] = []
        for role in roles:
            provider, model = self._mr.resolve_role(role)
            try:
                logger.info("fallback chain trying role=%s (%s/%s)",
                            role, provider, model)
                return await self._mr.invoke(
                    role=role, prompt=prompt, system=system,
                    max_tokens=max_tokens, temperature=temperature,
                    **kwargs,
                )
            except Exception as e:
                logger.warning(
                    "fallback chain role=%s (%s/%s) failed: %s",
                    role, provider, model, e,
                )
                attempts.append(FallbackAttempt(
                    role=role, provider=provider, model=model, error=str(e),
                ))

        raise LLMFallbackExhausted(attempts)


_singleton: FallbackRouter | None = None


def get_fallback_router(model_router=None) -> FallbackRouter:
    """Process-global :class:`FallbackRouter`.

    Lazy-builds over the default ModelRouter on first call.
    ``model_router`` lets tests inject a mock.
    """
    global _singleton
    if model_router is not None:
        # Explicit override — always returns a fresh wrapper so tests
        # don't pollute the singleton.
        return FallbackRouter(model_router)
    if _singleton is None:
        from llm.router import ModelRouter
        _singleton = FallbackRouter(ModelRouter())
    return _singleton


def reset_for_tests() -> None:
    global _singleton
    _singleton = None
