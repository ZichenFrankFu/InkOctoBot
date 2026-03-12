"""Google Gemini API provider."""
from __future__ import annotations

import logging
from typing import Any, AsyncIterator

from .base import BaseLLMProvider, LLMMessage, LLMResponse, ProviderConfig, _safe_model_dump

logger = logging.getLogger("inkoctobot.models.gemini")

_DEFAULT_BASE = "https://generativelanguage.googleapis.com/v1beta"

_PRICING: dict[str, tuple[float, float]] = {
    "gemini-2.0-flash": (0.0001, 0.0004),
    "gemini-2.5-pro-preview-06-05": (0.00125, 0.01),
    "gemini-2.5-flash-preview-05-20": (0.00015, 0.0006),
}


class GeminiProvider(BaseLLMProvider):
    """Google Gemini provider using the OpenAI-compatible endpoint."""

    def __init__(self, config: ProviderConfig):
        super().__init__(config)
        try:
            from openai import AsyncOpenAI
        except ImportError:
            raise ImportError("pip install openai")
        if not config.api_key:
            raise ValueError("Gemini provider requires an API key")
        # Gemini exposes an OpenAI-compatible endpoint
        base = (config.base_url or _DEFAULT_BASE).rstrip("/")
        self._client = AsyncOpenAI(
            api_key=config.api_key,
            base_url=f"{base}/openai/",
        )

    async def generate(
        self, messages: list[LLMMessage], *,
        temperature: float | None = None, max_tokens: int | None = None,
        stop: list[str] | None = None, **kwargs: Any,
    ) -> LLMResponse:
        kw: dict[str, Any] = {
            "model": self.config.model_name or "gemini-2.0-flash",
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "temperature": temperature if temperature is not None else self.config.temperature,
            "max_tokens": max_tokens or self.config.max_tokens,
        }
        if stop:
            kw["stop"] = stop
        kw.update(kwargs)
        resp = await self._client.chat.completions.create(**kw)
        choice = resp.choices[0]
        usage = resp.usage
        return LLMResponse(
            content=choice.message.content or "",
            model=resp.model,
            input_tokens=usage.prompt_tokens if usage else 0,
            output_tokens=usage.completion_tokens if usage else 0,
            finish_reason=choice.finish_reason or "",
            raw=_safe_model_dump(resp),
        )

    async def generate_stream(
        self, messages: list[LLMMessage], *,
        temperature: float | None = None, max_tokens: int | None = None,
        stop: list[str] | None = None, **kwargs: Any,
    ) -> AsyncIterator[str]:
        kw: dict[str, Any] = {
            "model": self.config.model_name or "gemini-2.0-flash",
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "temperature": temperature if temperature is not None else self.config.temperature,
            "max_tokens": max_tokens or self.config.max_tokens,
            "stream": True,
        }
        if stop:
            kw["stop"] = stop
        kw.update(kwargs)
        stream = await self._client.chat.completions.create(**kw)
        async for chunk in stream:
            delta = chunk.choices[0].delta if chunk.choices else None
            if delta and delta.content:
                yield delta.content

    def estimate_cost(self, input_tokens: int, output_tokens: int) -> float:
        prices = _PRICING.get(self.config.model_name, (0.0001, 0.0004))
        return (input_tokens / 1000 * prices[0]) + (output_tokens / 1000 * prices[1])
