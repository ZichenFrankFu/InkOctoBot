"""
Model router — dispatches LLM calls by agent role.

Reads config/models.yaml to determine which provider + model each agent
role should use.  Falls back to a sensible default when unconfigured.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, AsyncIterator

import yaml

from agents.model_providers.base import (
    BaseLLMProvider, LLMMessage, LLMResponse, ProviderConfig,
)

logger = logging.getLogger("inkoctobot.agents.model_router")

_CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"

_PROVIDER_CLS: dict[str, str] = {
    "openai":    "agents.model_providers.openai_provider.OpenAIProvider",
    "anthropic": "agents.model_providers.anthropic_provider.AnthropicProvider",
    "deepseek":  "agents.model_providers.deepseek_provider.DeepSeekProvider",
    "ollama":    "agents.model_providers.ollama_provider.OllamaProvider",
    "vllm":      "agents.model_providers.vllm_provider.VLLMProvider",
    "lora":      "agents.model_providers.lora_provider.LoRAProvider",
}


def _import_class(dotpath: str) -> type:
    mod_path, cls_name = dotpath.rsplit(".", 1)
    import importlib
    mod = importlib.import_module(mod_path)
    return getattr(mod, cls_name)


class ModelRouter:
    """Route LLM requests to the right provider based on agent role."""

    def __init__(self, *, config_path: str | Path | None = None, api_keys: dict[str, str] | None = None):
        self._providers: dict[str, BaseLLMProvider] = {}
        self._role_map: dict[str, str] = {}       # agent_role -> provider_key
        self._api_keys = api_keys or {}
        self._config = self._load_config(config_path)
        self._build_providers()

    def _load_config(self, path: str | Path | None) -> dict[str, Any]:
        if path is None:
            path = _CONFIG_DIR / "models.yaml"
        p = Path(path)
        if not p.exists() or p.stat().st_size == 0:
            return self._default_config()
        with open(p, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or self._default_config()

    @staticmethod
    def _default_config() -> dict[str, Any]:
        return {
            "default_provider": "ollama",
            "default_model": "qwen2.5:14b",
            "role_overrides": {},
        }

    def _build_providers(self) -> None:
        providers_json = _CONFIG_DIR / "model_providers.json"
        provider_defs: dict[str, Any] = {}
        if providers_json.exists():
            with open(providers_json, "r", encoding="utf-8") as f:
                provider_defs = json.load(f).get("providers", {})

        default_type = self._config.get("default_provider", "ollama")
        default_model = self._config.get("default_model", "qwen2.5:14b")

        # Build default provider
        self._ensure_provider(
            f"default_{default_type}",
            default_type,
            default_model,
            provider_defs.get(default_type, {}),
        )

        # Build role-specific overrides
        for role, spec in self._config.get("role_overrides", {}).items():
            ptype = spec.get("provider", default_type)
            model = spec.get("model", default_model)
            key = f"{role}_{ptype}_{model}"
            self._ensure_provider(key, ptype, model, provider_defs.get(ptype, {}), spec)
            self._role_map[role] = key

    def _ensure_provider(
        self, key: str, provider_type: str, model_name: str,
        provider_def: dict[str, Any], spec: dict[str, Any] | None = None,
    ) -> None:
        if key in self._providers:
            return
        cls_path = _PROVIDER_CLS.get(provider_type)
        if not cls_path:
            logger.warning("Unknown provider type: %s", provider_type)
            return
        cfg = ProviderConfig(
            provider_type=provider_type,
            model_name=model_name,
            base_url=provider_def.get("base_url") or (spec or {}).get("base_url"),
            api_key=self._api_keys.get(provider_type) or (spec or {}).get("api_key"),
            max_tokens=(spec or {}).get("max_tokens", 4096),
            temperature=(spec or {}).get("temperature", 0.7),
            extra=(spec or {}).get("extra", {}),
        )
        try:
            cls = _import_class(cls_path)
            self._providers[key] = cls(cfg)
            logger.info("Provider ready: %s -> %s/%s", key, provider_type, model_name)
        except Exception as e:
            logger.error("Failed to init provider %s: %s", key, e)

    def _get_provider(self, agent_role: str) -> BaseLLMProvider:
        key = self._role_map.get(agent_role)
        if key and key in self._providers:
            return self._providers[key]
        default_type = self._config.get("default_provider", "ollama")
        return self._providers[f"default_{default_type}"]

    async def generate(
        self, *, agent_role: str, messages: list[LLMMessage],
        temperature: float | None = None, max_tokens: int | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        provider = self._get_provider(agent_role)
        logger.debug("Routing %s -> %s", agent_role, provider)
        return await provider.generate(
            messages, temperature=temperature, max_tokens=max_tokens, **kwargs,
        )

    async def generate_stream(
        self, *, agent_role: str, messages: list[LLMMessage],
        temperature: float | None = None, max_tokens: int | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[str]:
        provider = self._get_provider(agent_role)
        async for token in provider.generate_stream(
            messages, temperature=temperature, max_tokens=max_tokens, **kwargs,
        ):
            yield token

    def estimate_cost(self, agent_role: str, input_tokens: int, output_tokens: int) -> float:
        return self._get_provider(agent_role).estimate_cost(input_tokens, output_tokens)

    def list_providers(self) -> dict[str, str]:
        return {k: repr(v) for k, v in self._providers.items()}
