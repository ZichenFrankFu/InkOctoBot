"""Ollama local model provider (REST API)."""
from __future__ import annotations

import json, logging
from typing import Any, AsyncIterator

from .base import BaseLLMProvider, LLMMessage, LLMResponse, ProviderConfig

logger = logging.getLogger("inkoctobot.agents.model_providers.ollama")
_DEFAULT_BASE = "http://localhost:11434"


class OllamaProvider(BaseLLMProvider):

    def __init__(self, config: ProviderConfig):
        if not config.base_url:
            config.base_url = _DEFAULT_BASE
        super().__init__(config)
        self._base = config.base_url.rstrip("/")

    async def generate(self, messages: list[LLMMessage], *, temperature: float | None = None,
                       max_tokens: int | None = None, stop: list[str] | None = None, **kw: Any) -> LLMResponse:
        import httpx
        payload: dict[str, Any] = {
            "model": self.config.model_name,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "stream": False,
            "options": {"temperature": temperature if temperature is not None else self.config.temperature},
        }
        if max_tokens or self.config.max_tokens:
            payload["options"]["num_predict"] = max_tokens or self.config.max_tokens
        if stop:
            payload["options"]["stop"] = stop
        async with httpx.AsyncClient(timeout=300) as client:
            r = await client.post(f"{self._base}/api/chat", json=payload)
            r.raise_for_status()
            data = r.json()
        msg = data.get("message", {})
        return LLMResponse(
            content=msg.get("content", ""), model=data.get("model", self.config.model_name),
            input_tokens=data.get("prompt_eval_count", 0), output_tokens=data.get("eval_count", 0),
            finish_reason=data.get("done_reason", "stop"), raw=data,
        )

    async def generate_stream(self, messages: list[LLMMessage], *, temperature: float | None = None,
                              max_tokens: int | None = None, stop: list[str] | None = None, **kw: Any) -> AsyncIterator[str]:
        import httpx
        payload: dict[str, Any] = {
            "model": self.config.model_name,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "stream": True,
            "options": {"temperature": temperature if temperature is not None else self.config.temperature},
        }
        if max_tokens or self.config.max_tokens:
            payload["options"]["num_predict"] = max_tokens or self.config.max_tokens
        if stop:
            payload["options"]["stop"] = stop
        async with httpx.AsyncClient(timeout=300) as client:
            async with client.stream("POST", f"{self._base}/api/chat", json=payload) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line.strip():
                        continue
                    data = json.loads(line)
                    c = data.get("message", {}).get("content", "")
                    if c:
                        yield c

    async def health_check(self) -> bool:
        import httpx
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                return (await client.get(f"{self._base}/api/tags")).status_code == 200
        except Exception:
            return False
