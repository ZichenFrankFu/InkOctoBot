"""
Multi-model A/B comparison engine.

Runs the same prompt through multiple providers and returns
side-by-side results for user comparison.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

from agents.model_providers.base import LLMMessage, LLMResponse

logger = logging.getLogger("inkoctobot.agents.ab_compare")


@dataclass
class CompareResult:
    """Side-by-side results from multiple models."""
    prompt: str
    responses: dict[str, LLMResponse] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "prompt_preview": self.prompt[:200],
            "results": {
                k: {"content": v.content[:500], "model": v.model,
                     "tokens": v.total_tokens}
                for k, v in self.responses.items()
            },
            "errors": self.errors,
        }


class ABCompareEngine:
    """Run identical prompts through multiple models for comparison."""

    def __init__(self, router: Any):
        self.router = router

    async def compare(
        self,
        messages: list[LLMMessage],
        agent_roles: list[str],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> CompareResult:
        prompt_text = messages[-1].content if messages else ""
        result = CompareResult(prompt=prompt_text)

        async def _run(role: str) -> tuple[str, LLMResponse | None, str]:
            try:
                resp = await self.router.generate(
                    agent_role=role, messages=messages,
                    temperature=temperature, max_tokens=max_tokens,
                )
                return role, resp, ""
            except Exception as e:
                return role, None, str(e)

        tasks = [_run(r) for r in agent_roles]
        for coro in asyncio.as_completed(tasks):
            role, resp, err = await coro
            if resp:
                result.responses[role] = resp
            if err:
                result.errors[role] = err

        return result
