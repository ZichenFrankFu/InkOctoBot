"""
Base provider interface.

All LLM providers implement this abstract interface so the model router
can treat them uniformly.

Migrated from agents/model_providers/base.py.
"""
from __future__ import annotations

import abc
import logging
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

logger = logging.getLogger("inkoctobot.models.base")


@dataclass
class LLMMessage:
    """A single message in a conversation."""
    role: str          # "system" | "user" | "assistant"
    content: str


@dataclass
class LLMResponse:
    """Unified response from any provider."""
    content: str
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    finish_reason: str = ""
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass
class ProviderConfig:
    """Provider-level configuration."""
    provider_type: str        # "openai" | "anthropic" | "deepseek" | "gemini" | "ollama" | "vllm" | "lora"
    base_url: str | None = None
    api_key: str | None = None
    model_name: str = ""
    max_tokens: int = 4096
    temperature: float = 0.7
    top_p: float = 0.95
    extra: dict[str, Any] = field(default_factory=dict)


class BaseLLMProvider(abc.ABC):
    """Abstract LLM provider."""

    def __init__(self, config: ProviderConfig):
        self.config = config
        self._logger = logging.getLogger(
            f"inkoctobot.models.{config.provider_type}"
        )

    @property
    def provider_type(self) -> str:
        return self.config.provider_type

    @property
    def model_name(self) -> str:
        return self.config.model_name

    @abc.abstractmethod
    async def generate(
        self,
        messages: list[LLMMessage],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        stop: list[str] | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        """Generate a completion from the given messages."""
        ...

    async def generate_stream(
        self,
        messages: list[LLMMessage],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        stop: list[str] | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[str]:
        """Stream tokens. Default falls back to non-streaming."""
        resp = await self.generate(
            messages, temperature=temperature,
            max_tokens=max_tokens, stop=stop, **kwargs,
        )
        yield resp.content

    async def health_check(self) -> bool:
        """Return True if the provider is reachable."""
        try:
            resp = await self.generate(
                [LLMMessage(role="user", content="ping")], max_tokens=8,
            )
            return bool(resp.content)
        except Exception:
            return False

    def estimate_cost(self, input_tokens: int, output_tokens: int) -> float:
        """Estimate cost in USD. Override for commercial providers."""
        return 0.0

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} model={self.model_name}>"
