"""OpenAI API provider — supports any OpenAI-compatible API."""
from __future__ import annotations

import logging
from typing import Any, AsyncIterator

from .base import BaseLLMProvider, LLMMessage, LLMResponse, ProviderConfig

logger = logging.getLogger("inkoctobot.agents.model_providers.openai")

_PRICING: dict[str, tuple[float, float]] = {
    "gpt-4o":        (0.005, 0.015),
    "gpt-4o-mini":   (0.00015, 0.0006),
    "gpt-4-turbo":   (0.01, 0.03),
}


class OpenAIProvider(BaseLLMProvider):

    def __init__(self, config: ProviderConfig):
        super().__init__(config)
        try:
            from openai import AsyncOpenAI
        except ImportError:
            raise ImportError("pip install openai")
        kw: dict[str, Any] = {}
        if config.api_key:
            kw["api_key"] = config.api_key
        if config.base_url:
            kw["base_url"] = config.base_url
        self._client = AsyncOpenAI(**kw)

    async def generate(
        self, messages: list[LLMMessage], *,
        temperature: float | None = None, max_tokens: int | None = None,
        stop: list[str] | None = None, **kwargs: Any,
    ) -> LLMResponse:
        resp = await self._client.chat.completions.create(
            model=self.config.model_name,
            messages=[{"role": m.role, "content": m.content} for m in messages],
            temperature=temperature if temperature is not None else self.config.temperature,
            max_tokens=max_tokens or self.config.max_tokens,
            stop=stop, **kwargs,
        )
        choice = resp.choices[0]
        usage = resp.usage
        return LLMResponse(
            content=choice.message.content or "", model=resp.model,
            input_tokens=usage.prompt_tokens if usage else 0,
            output_tokens=usage.completion_tokens if usage else 0,
            finish_reason=choice.finish_reason or "", raw=resp.model_dump(),
        )

    async def generate_stream(
        self, messages: list[LLMMessage], *,
        temperature: float | None = None, max_tokens: int | None = None,
        stop: list[str] | None = None, **kwargs: Any,
    ) -> AsyncIterator[str]:
        stream = await self._client.chat.completions.create(
            model=self.config.model_name,
            messages=[{"role": m.role, "content": m.content} for m in messages],
            temperature=temperature if temperature is not None else self.config.temperature,
            max_tokens=max_tokens or self.config.max_tokens,
            stop=stop, stream=True, **kwargs,
        )
        async for chunk in stream:
            delta = chunk.choices[0].delta if chunk.choices else None
            if delta and delta.content:
                yield delta.content

    def estimate_cost(self, input_tokens: int, output_tokens: int) -> float:
        prices = _PRICING.get(self.config.model_name, (0.005, 0.015))
        return (input_tokens / 1000 * prices[0]) + (output_tokens / 1000 * prices[1])
