"""
Model router — dispatches LLM calls by agent role.

Reads config/models.yaml to determine which provider + model each agent
role should use. Falls back to a sensible default when unconfigured.

Migrated from agents/model_router.py.
"""
from __future__ import annotations

import importlib
import json
import logging
from functools import lru_cache
from pathlib import Path
from typing import Any, AsyncIterator

import yaml

from llm.base import (
    BaseLLMProvider, LLMMessage, LLMResponse, ProviderConfig,
)

logger = logging.getLogger("inkoctobot.llm.router")

_CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"

_PROVIDER_CLS: dict[str, str] = {
    "openai":    "llm.openai_provider.OpenAIProvider",
    "anthropic": "llm.anthropic_provider.AnthropicProvider",
    "deepseek":  "llm.deepseek_provider.DeepSeekProvider",
    "gemini":    "llm.gemini_provider.GeminiProvider",
    "ollama":    "llm.ollama_provider.OllamaProvider",
    "vllm":      "llm.vllm_provider.VLLMProvider",
    "mock":      "llm.mock_provider.MockProvider",
}


@lru_cache(maxsize=32)
def _import_class(dotpath: str) -> type:
    mod_path, cls_name = dotpath.rsplit(".", 1)
    mod = importlib.import_module(mod_path)
    return getattr(mod, cls_name)


class ModelRouter:
    """Route LLM requests to the right provider based on agent role."""

    def __init__(
        self,
        *,
        config_path: str | Path | None = None,
        api_keys: dict[str, str] | None = None,
    ):
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
            cfg = self._default_config()
        else:
            with open(p, "r", encoding="utf-8") as f:
                cfg = yaml.safe_load(f) or self._default_config()
        # Merge user-edited Settings (data/settings.json) on top of the
        # static YAML. The UI writes per-role pipeline assignments and
        # per-provider api_key/base_url/models there; without this merge
        # the router silently keeps using the YAML's placeholder values
        # (e.g. ollama/qwen2.5:14b) even after the user re-binds the role.
        cfg.setdefault("role_overrides", {})
        cfg.setdefault("_ui_providers", {})
        try:
            ui = self._load_ui_settings()
        except Exception as e:
            logger.warning("could not merge data/settings.json: %s", e)
            ui = {}
        # 1) Per-role overrides from Settings → Pipeline 配置.
        for role, asg in (ui.get("pipeline") or {}).items():
            if not isinstance(asg, dict):
                continue
            prov = (asg.get("provider") or "").strip()
            mdl = (asg.get("model") or "").strip()
            if not prov or not mdl:
                continue  # blank row in UI; keep YAML default
            existing = cfg["role_overrides"].setdefault(role, {})
            existing["provider"] = prov
            existing["model"] = mdl
        # 2) Per-provider api_key + base_url + models from Settings →
        #    模型供应商. Stored on cfg for _build_providers to consume.
        cfg["_ui_providers"] = ui.get("providers") or {}
        return cfg

    @staticmethod
    def _load_ui_settings() -> dict[str, Any]:
        """Read data/settings.json (the file the SettingsPage saves to).
        Returns {} if the file doesn't exist or fails to parse."""
        # Same resolution as ui/backend/app/routers/settings_api.py
        candidates = [
            _CONFIG_DIR.parent / "data" / "settings.json",
            Path.cwd() / "data" / "settings.json",
        ]
        for c in candidates:
            if c.exists():
                with open(c, "r", encoding="utf-8") as f:
                    return json.load(f)
        return {}

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
            with open(providers_json, "r", encoding="utf-8-sig") as f:
                provider_defs = json.load(f).get("providers", {})

        # Merge the user-edited provider config (api_key + base_url) on
        # top of the static defaults so a user-supplied api_key in
        # data/settings.json takes precedence over the bare provider
        # definition shipped in config/model_providers.json.
        ui_providers: dict[str, Any] = self._config.get("_ui_providers") or {}
        for name, ucfg in ui_providers.items():
            if not isinstance(ucfg, dict):
                continue
            merged = dict(provider_defs.get(name, {}))
            if ucfg.get("api_key"):
                merged["api_key"] = ucfg["api_key"]
            if ucfg.get("base_url"):
                merged["base_url"] = ucfg["base_url"]
            provider_defs[name] = merged

        default_type = self._config.get("default_provider", "ollama")
        default_model = self._config.get("default_model", "qwen2.5:14b")

        self._ensure_provider(
            f"default_{default_type}",
            default_type,
            default_model,
            provider_defs.get(default_type, {}),
        )

        for role, spec in self._config.get("role_overrides", {}).items():
            ptype = spec.get("provider", default_type)
            model = spec.get("model", default_model)
            key = f"{role}_{ptype}_{model}"
            self._ensure_provider(
                key, ptype, model, provider_defs.get(ptype, {}), spec,
            )
            self._role_map[role] = key

    def _ensure_provider(
        self,
        key: str,
        provider_type: str,
        model_name: str,
        provider_def: dict[str, Any],
        spec: dict[str, Any] | None = None,
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
        self,
        *,
        agent_role: str,
        messages: list[LLMMessage],
        temperature: float | None = None,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        provider = self._get_provider(agent_role)
        # Log the actual provider+model dispatched so failures are never
        # opaque (was GAP 5 in the architecture review). Provider-side
        # errors get exc_info so the stack trace lands in the log buffer.
        model_name = getattr(provider, "model_name", "?")
        prov_name = type(provider).__name__
        logger.info("route role=%s provider=%s model=%s",
                    agent_role, prov_name, model_name)
        try:
            return await provider.generate(
                messages, temperature=temperature, max_tokens=max_tokens, **kwargs,
            )
        except Exception:
            logger.exception(
                "provider FAILED role=%s provider=%s model=%s",
                agent_role, prov_name, model_name,
            )
            raise

    async def generate_stream(
        self,
        *,
        agent_role: str,
        messages: list[LLMMessage],
        temperature: float | None = None,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[str]:
        provider = self._get_provider(agent_role)
        model_name = getattr(provider, "model_name", "?")
        prov_name = type(provider).__name__
        logger.info("stream role=%s provider=%s model=%s",
                    agent_role, prov_name, model_name)
        async for token in provider.generate_stream(
            messages, temperature=temperature, max_tokens=max_tokens, **kwargs,
        ):
            yield token

    async def invoke(
        self,
        *,
        role: str,
        prompt: str,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        system: str | None = None,
        **kwargs: Any,
    ) -> str:
        """Simplified invoke for Skill execution (prompt-in, text-out).

        Extra ``kwargs`` (e.g. ``response_format={"type": "json_object"}``)
        are forwarded to the underlying provider. Providers that don't
        understand a keyword silently ignore it — but if the OpenAI SDK
        rejects an unknown arg we catch the error and retry without it,
        so callers can opportunistically request JSON mode without having
        to know the active provider."""
        messages: list[LLMMessage] = []
        if system:
            messages.append(LLMMessage(role="system", content=system))
        messages.append(LLMMessage(role="user", content=prompt))
        try:
            resp = await self.generate(
                agent_role=role,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                **kwargs,
            )
        except TypeError:
            # Provider doesn't accept one of the kwargs — retry plain.
            resp = await self.generate(
                agent_role=role,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )
        except Exception as e:
            # Some OpenAI-compat servers (e.g., older DeepSeek) raise
            # BadRequestError on `response_format`. Strip and retry.
            if kwargs and "response_format" in str(e).lower():
                kwargs.pop("response_format", None)
                resp = await self.generate(
                    agent_role=role,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    **kwargs,
                )
            else:
                raise
        return resp.content

    async def invoke_with_web_search(
        self,
        *,
        role: str,
        prompt: str,
        max_tokens: int = 4096,
        temperature: float = 0.3,
    ) -> str:
        """Like ``invoke`` but requires the resolved provider to expose
        ``generate_with_web_search``. Raises NotImplementedError when the
        configured provider/model doesn't support web search."""
        provider = self._get_provider(role)
        messages = [LLMMessage(role="user", content=prompt)]
        resp = await provider.generate_with_web_search(
            messages, temperature=temperature, max_tokens=max_tokens,
        )
        return resp.content

    def resolve_role(self, role: str) -> tuple[str, str]:
        """Return (provider_type, model_name) currently bound to ``role``.
        Returns ("", "") if unbound."""
        prov = self._get_provider(role)
        if prov is None:
            return ("", "")
        return (prov.provider_type, prov.model_name)

    def estimate_cost(
        self, agent_role: str, input_tokens: int, output_tokens: int,
    ) -> float:
        return self._get_provider(agent_role).estimate_cost(input_tokens, output_tokens)

    def list_providers(self) -> dict[str, str]:
        return {k: repr(v) for k, v in self._providers.items()}
