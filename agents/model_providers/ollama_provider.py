"""Ollama local model provider (REST API).

Supports both /api/chat (Ollama >= 0.1.14) and /api/generate (legacy fallback).
"""
from __future__ import annotations

import json, logging
from typing import Any, AsyncIterator

from .base import BaseLLMProvider, LLMMessage, LLMResponse, ProviderConfig

logger = logging.getLogger("inkoctobot.agents.model_providers.ollama")
_DEFAULT_BASE = "http://localhost:11434"


def _messages_to_prompt(messages: list[LLMMessage]) -> str:
    """Convert chat messages to a single prompt string for /api/generate."""
    parts: list[str] = []
    for m in messages:
        if m.role == "system":
            parts.append(f"[System]\n{m.content}\n")
        elif m.role == "user":
            parts.append(f"[User]\n{m.content}\n")
        elif m.role == "assistant":
            parts.append(f"[Assistant]\n{m.content}\n")
    parts.append("[Assistant]\n")
    return "\n".join(parts)


class OllamaProvider(BaseLLMProvider):

    def __init__(self, config: ProviderConfig):
        if not config.base_url:
            config.base_url = _DEFAULT_BASE
        super().__init__(config)
        self._base = config.base_url.rstrip("/")
        # Will be set to True if /api/chat returns 404
        self._use_generate_fallback = False

    async def _verify_model(self, client) -> None:
        """Check that the model exists on the Ollama server."""
        if not self.config.model_name:
            # Try to pick the first available model
            try:
                r = await client.get(f"{self._base}/api/tags")
                if r.status_code == 200:
                    models = [m["name"] for m in r.json().get("models", [])]
                    if models:
                        self.config.model_name = models[0]
                        logger.info("Auto-selected Ollama model: %s", self.config.model_name)
                        return
            except Exception:
                pass
            raise ValueError(
                "Ollama 模型名称为空。请在「设置→模型供应商→Ollama」中点击"
                "「自动检测」，或手动填写已安装的模型名称。"
            )

    async def generate(self, messages: list[LLMMessage], *, temperature: float | None = None,
                       max_tokens: int | None = None, stop: list[str] | None = None, **kw: Any) -> LLMResponse:
        import httpx
        options: dict[str, Any] = {
            "temperature": temperature if temperature is not None else self.config.temperature,
        }
        if max_tokens or self.config.max_tokens:
            options["num_predict"] = max_tokens or self.config.max_tokens
        if stop:
            options["stop"] = stop

        async with httpx.AsyncClient(timeout=300) as client:
            await self._verify_model(client)

            if not self._use_generate_fallback:
                # Try /api/chat first
                payload = {
                    "model": self.config.model_name,
                    "messages": [{"role": m.role, "content": m.content} for m in messages],
                    "stream": False,
                    "options": options,
                }
                try:
                    r = await client.post(f"{self._base}/api/chat", json=payload)
                    if r.status_code == 404:
                        err_body = r.text
                        # Check if it's model-not-found vs endpoint-not-found
                        if "not found" in err_body.lower() and "model" in err_body.lower():
                            raise ValueError(
                                f"Ollama 模型 '{self.config.model_name}' 不存在。"
                                f"请运行 `ollama list` 确认模型名称，"
                                f"或在设置中点击「自动检测」。"
                            )
                        # Endpoint doesn't exist — fall back to /api/generate
                        logger.info("Ollama /api/chat returned 404, falling back to /api/generate")
                        self._use_generate_fallback = True
                    else:
                        r.raise_for_status()
                        data = r.json()
                        msg = data.get("message", {})
                        return LLMResponse(
                            content=msg.get("content", ""),
                            model=data.get("model", self.config.model_name),
                            input_tokens=data.get("prompt_eval_count", 0),
                            output_tokens=data.get("eval_count", 0),
                            finish_reason=data.get("done_reason", "stop"),
                            raw=data,
                        )
                except httpx.HTTPStatusError as e:
                    if e.response.status_code == 404:
                        logger.info("Ollama /api/chat HTTP 404, falling back to /api/generate")
                        self._use_generate_fallback = True
                    else:
                        raise

            # Fallback: /api/generate
            payload = {
                "model": self.config.model_name,
                "prompt": _messages_to_prompt(messages),
                "stream": False,
                "options": options,
            }
            r = await client.post(f"{self._base}/api/generate", json=payload)
            if r.status_code == 404:
                err_body = r.text
                raise ValueError(
                    f"Ollama 无法找到模型或端点。"
                    f"请确认 Ollama 正在运行且模型已安装。\n"
                    f"模型名: {self.config.model_name}\n"
                    f"服务地址: {self._base}\n"
                    f"错误: {err_body[:200]}"
                )
            r.raise_for_status()
            data = r.json()
            return LLMResponse(
                content=data.get("response", ""),
                model=data.get("model", self.config.model_name),
                input_tokens=data.get("prompt_eval_count", 0),
                output_tokens=data.get("eval_count", 0),
                finish_reason=data.get("done_reason", "stop"),
                raw=data,
            )

    async def generate_stream(self, messages: list[LLMMessage], *, temperature: float | None = None,
                              max_tokens: int | None = None, stop: list[str] | None = None, **kw: Any) -> AsyncIterator[str]:
        import httpx
        options: dict[str, Any] = {
            "temperature": temperature if temperature is not None else self.config.temperature,
        }
        if max_tokens or self.config.max_tokens:
            options["num_predict"] = max_tokens or self.config.max_tokens
        if stop:
            options["stop"] = stop

        async with httpx.AsyncClient(timeout=300) as client:
            await self._verify_model(client)

            if not self._use_generate_fallback:
                # Try /api/chat first
                payload = {
                    "model": self.config.model_name,
                    "messages": [{"role": m.role, "content": m.content} for m in messages],
                    "stream": True,
                    "options": options,
                }
                try:
                    async with client.stream("POST", f"{self._base}/api/chat", json=payload) as resp:
                        if resp.status_code == 404:
                            logger.info("Ollama /api/chat stream 404, falling back to /api/generate")
                            self._use_generate_fallback = True
                        else:
                            resp.raise_for_status()
                            async for line in resp.aiter_lines():
                                if not line.strip():
                                    continue
                                data = json.loads(line)
                                c = data.get("message", {}).get("content", "")
                                if c:
                                    yield c
                            return
                except httpx.HTTPStatusError as e:
                    if e.response.status_code == 404:
                        self._use_generate_fallback = True
                    else:
                        raise

            # Fallback: /api/generate
            payload = {
                "model": self.config.model_name,
                "prompt": _messages_to_prompt(messages),
                "stream": True,
                "options": options,
            }
            async with client.stream("POST", f"{self._base}/api/generate", json=payload) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line.strip():
                        continue
                    data = json.loads(line)
                    c = data.get("response", "")
                    if c:
                        yield c

    async def health_check(self) -> bool:
        import httpx
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                return (await client.get(f"{self._base}/api/tags")).status_code == 200
        except Exception:
            return False
