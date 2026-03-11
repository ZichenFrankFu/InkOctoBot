"""Backward-compatibility shim — imports from models.base."""
from models.base import (  # noqa: F401
    LLMMessage,
    LLMResponse,
    ProviderConfig,
    BaseLLMProvider,
)
