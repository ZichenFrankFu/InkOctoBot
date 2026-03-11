"""Anthropic API provider."""
from __future__ import annotations

import logging
from typing import Any, AsyncIterator

from .base import BaseLLMProvider, LLMMessage, LLMResponse, ProviderConfig

logger = logging.getLogger("inkoctobot.models.anthropic")

_PRICING: dict[str, tuple[float, float]] = {
    "claude-sonnet-4-6":          (0.003, 0.015),
    "claude-opus-4-6":            (0.015, 0.075),
    "claude-haiku-4-5-20251001":  (0.0008, 0.004),
}


class AnthropicProvider(BaseLLMProvider):

    def __init__(self, config: ProviderConfig):
        super().__init__(config)
        try:
            from anthropic import AsyncAnthropic
        except ImportError:
            raise ImportError("pip install anthropic")
        kw: dict[str, Any] = {}
        if config.api_key:
            kw["api_key"] = config.api_key
        if config.base_url:
            kw["base_url"] = config.base_url
        self._client = AsyncAnthropic(**kw)

    def _split(self, messages: list[LLMMessage]) -> tuple[str, list[dict]]:
        sys_parts, api_msgs = [], []
        for m in messages:
            if m.role == "system":
                sys_parts.append(m.content)
            else:
                api_msgs.append({"role": m.role, "content": m.content})
        if not api_msgs:
            api_msgs = [{"role": "user", "content": "..."}]
        return "\n".join(sys_parts), api_msgs

    async def generate(
        self, messages: list[LLMMessage], *,
        temperature: float | None = None, max_tokens: int | None = None,
        stop: list[str] | None = None, **kwargs: Any,
    ) -> LLMResponse:
        sys_text, api_msgs = self._split(messages)
        kw: dict[str, Any] = {
            "model": self.config.model_name, "messages": api_msgs,
            "max_tokens": max_tokens or self.config.max_tokens,
            "temperature": temperature if temperature is not None else self.config.temperature,
        }
        if sys_text:
            kw["system"] = sys_text
        if stop:
            kw["stop_sequences"] = stop
        resp = await self._client.messages.create(**kw)
        text = resp.content[0].text if resp.content else ""
        return LLMResponse(
            content=text, model=resp.model,
            input_tokens=resp.usage.input_tokens,
            output_tokens=resp.usage.output_tokens,
            finish_reason=resp.stop_reason or "",
            raw={"id": resp.id, "model": resp.model},
        )

    async def generate_stream(
        self, messages: list[LLMMessage], *,
        temperature: float | None = None, max_tokens: int | None = None,
        stop: list[str] | None = None, **kwargs: Any,
    ) -> AsyncIterator[str]:
        sys_text, api_msgs = self._split(messages)
        kw: dict[str, Any] = {
            "model": self.config.model_name, "messages": api_msgs,
            "max_tokens": max_tokens or self.config.max_tokens,
            "temperature": temperature if temperature is not None else self.config.temperature,
        }
        if sys_text:
            kw["system"] = sys_text
        if stop:
            kw["stop_sequences"] = stop
        async with self._client.messages.stream(**kw) as stream:
            async for text in stream.text_stream:
                yield text

    def estimate_cost(self, input_tokens: int, output_tokens: int) -> float:
        prices = _PRICING.get(self.config.model_name, (0.003, 0.015))
        return (input_tokens / 1000 * prices[0]) + (output_tokens / 1000 * prices[1])
