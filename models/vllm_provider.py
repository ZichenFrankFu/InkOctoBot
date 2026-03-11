"""vLLM local inference provider (OpenAI-compatible API)."""
from __future__ import annotations

import logging
from typing import Any, AsyncIterator

from .base import BaseLLMProvider, LLMMessage, LLMResponse, ProviderConfig

logger = logging.getLogger("inkoctobot.models.vllm")


class VLLMProvider(BaseLLMProvider):

    def __init__(self, config: ProviderConfig):
        if not config.base_url:
            config.base_url = "http://localhost:8000/v1"
        super().__init__(config)
        try:
            from openai import AsyncOpenAI
        except ImportError:
            raise ImportError("pip install openai")
        self._client = AsyncOpenAI(api_key="EMPTY", base_url=config.base_url)

    async def generate(self, messages: list[LLMMessage], *, temperature: float | None = None,
                       max_tokens: int | None = None, stop: list[str] | None = None, **kw: Any) -> LLMResponse:
        resp = await self._client.chat.completions.create(
            model=self.config.model_name,
            messages=[{"role": m.role, "content": m.content} for m in messages],
            temperature=temperature if temperature is not None else self.config.temperature,
            max_tokens=max_tokens or self.config.max_tokens, stop=stop, **kw,
        )
        c, u = resp.choices[0], resp.usage
        return LLMResponse(content=c.message.content or "", model=resp.model,
                           input_tokens=u.prompt_tokens if u else 0,
                           output_tokens=u.completion_tokens if u else 0,
                           finish_reason=c.finish_reason or "", raw=resp.model_dump())

    async def generate_stream(self, messages: list[LLMMessage], *, temperature: float | None = None,
                              max_tokens: int | None = None, stop: list[str] | None = None, **kw: Any) -> AsyncIterator[str]:
        stream = await self._client.chat.completions.create(
            model=self.config.model_name,
            messages=[{"role": m.role, "content": m.content} for m in messages],
            temperature=temperature if temperature is not None else self.config.temperature,
            max_tokens=max_tokens or self.config.max_tokens, stop=stop, stream=True, **kw,
        )
        async for chunk in stream:
            d = chunk.choices[0].delta if chunk.choices else None
            if d and d.content:
                yield d.content
