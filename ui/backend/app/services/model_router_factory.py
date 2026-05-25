"""LLM router construction from user settings.

The Settings page writes pipeline assignments (which agent role uses
which provider+model) plus per-provider config (api_key, base_url) to
``data/settings.json``. This module reads that file and produces a
router that resolves the right provider on every ``generate()`` call.

The router exposes the minimal surface BaseAgent / BaseSkill need:
``generate(agent_role=..., messages=..., ...)``,
``generate_stream(...)``, and ``invoke(role=..., prompt=...)``.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

from .usage_tracker import record_usage

logger = logging.getLogger("inkoctobot.services.model_router_factory")


# ────────────────────────────────────────────────────────────────────
# User settings loader (merges defaults so new providers/roles appear)
# ────────────────────────────────────────────────────────────────────

def get_user_settings() -> dict:
    """Load ``data/settings.json``, merging in defaults for missing keys."""
    from ui.backend.app.settings import settings as app_settings
    from ui.backend.app.routers.json_storage_api import _default_settings

    p = app_settings.get_data_path("settings.json")
    data = json.loads(p.read_text("utf-8")) if p.exists() else {}

    defaults = _default_settings()
    for k, v in defaults.items():
        if k not in data:
            data[k] = v
    for pname, pdef in defaults.get("providers", {}).items():
        data.setdefault("providers", {}).setdefault(pname, pdef)
    for rname, rdef in defaults.get("pipeline", {}).items():
        data.setdefault("pipeline", {}).setdefault(rname, rdef)
    return data


# ────────────────────────────────────────────────────────────────────
# Provider instantiation
# ────────────────────────────────────────────────────────────────────

def make_provider_instance(cfg):
    """Instantiate a provider from a ProviderConfig.

    Unknown provider_type falls back to Ollama for robustness — the user
    might be mid-config when the call fires.
    """
    from llm.ollama_provider import OllamaProvider
    from llm.deepseek_provider import DeepSeekProvider
    from llm.openai_provider import OpenAIProvider
    from llm.anthropic_provider import AnthropicProvider
    from llm.gemini_provider import GeminiProvider
    from llm.vllm_provider import VLLMProvider
    from llm.mock_provider import MockProvider

    ptype = cfg.provider_type
    providers = {
        "ollama":    OllamaProvider,
        "deepseek":  DeepSeekProvider,
        "openai":    OpenAIProvider,
        "anthropic": AnthropicProvider,
        "gemini":    GeminiProvider,
        "vllm":      VLLMProvider,
        "mock":      MockProvider,
    }
    cls = providers.get(ptype, OllamaProvider)
    if ptype not in providers:
        logger.warning("unknown provider_type %r — falling back to ollama", ptype)
    return cls(cfg)


# ────────────────────────────────────────────────────────────────────
# Router class
# ────────────────────────────────────────────────────────────────────

class SimpleRouter:
    """Resolves provider+model per agent role from user settings.

    Caches one provider instance per ``provider:model`` key for the
    lifetime of the router (a router is typically scoped to a single
    generation session).
    """

    # Map BaseAgent agent_name → settings pipeline key.
    _ROLE_ALIASES: dict[str, str] = {
        "editor_writer":  "editor_stylist",
        "editor":         "editor_stylist",
        "scene_planner":  "scene_director",
        "actor":          "actor_default",
        "actors":         "actor_default",
        "actor_agent":    "actor_default",
        "narrator_agent": "actor_default",
    }

    def __init__(
        self,
        user_settings: dict,
        fallback_provider: str = "",
        fallback_model: str = "",
    ):
        self._settings = user_settings
        self._providers_cfg = user_settings.get("providers", {})
        self._pipeline = user_settings.get("pipeline", {})
        self._fallback_provider = fallback_provider
        self._fallback_model = fallback_model
        self._provider_cache: dict[str, Any] = {}

    def _resolve(self, agent_role: str) -> tuple[str, str, dict]:
        """Return (provider, model, provider_cfg) for the given agent role."""
        # In test mode, always use mock provider to avoid connection errors.
        if os.environ.get("WN_TEST_MODE") == "1":
            return "mock", "mock-test-v1", {}

        role_cfg = self._pipeline.get(agent_role, {})
        if not role_cfg.get("provider") and not role_cfg.get("model"):
            alias = self._ROLE_ALIASES.get(agent_role, "")
            if alias:
                role_cfg = self._pipeline.get(alias, {})
        provider = role_cfg.get("provider", "") or self._fallback_provider
        model = role_cfg.get("model", "") or self._fallback_model
        prov_cfg = self._providers_cfg.get(provider, {})
        if provider and not model:
            models = prov_cfg.get("models", [])
            if models:
                model = models[0]
        return provider, model, prov_cfg

    def _get_provider(self, provider: str, model: str, prov_cfg: dict):
        cache_key = f"{provider}:{model}"
        if cache_key in self._provider_cache:
            return self._provider_cache[cache_key]
        from llm.base import ProviderConfig
        cfg = ProviderConfig(
            provider_type=provider,
            model_name=model,
            base_url=prov_cfg.get("base_url") or None,
            api_key=prov_cfg.get("api_key") or None,
        )
        inst = make_provider_instance(cfg)
        self._provider_cache[cache_key] = inst
        return inst

    async def generate(
        self,
        *,
        agent_role: str,
        messages,
        temperature=None,
        max_tokens=None,
        **kw,
    ):
        provider, model, prov_cfg = self._resolve(agent_role)
        if not model:
            raise ValueError(
                f"角色 '{agent_role}' 未配置模型。请在「设置→Pipeline 配置」中分配。"
            )
        inst = self._get_provider(provider, model, prov_cfg)
        resp = await inst.generate(
            messages, temperature=temperature, max_tokens=max_tokens, **kw,
        )
        record_usage(agent_role, provider, model, resp.input_tokens, resp.output_tokens)
        return resp

    async def invoke(
        self,
        *,
        role: str,
        prompt: str,
        max_tokens: int = 4096,
        temperature: float = 0.7,
    ) -> str:
        """Simple prompt-in, text-out API used by BaseSkill.execute()."""
        from llm.base import LLMMessage
        messages = [LLMMessage(role="user", content=prompt)]
        resp = await self.generate(
            agent_role=role,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return resp.content

    async def generate_stream(
        self,
        *,
        agent_role: str,
        messages,
        temperature=None,
        max_tokens=None,
        **kw,
    ):
        provider, model, prov_cfg = self._resolve(agent_role)
        if not model:
            raise ValueError(
                f"角色 '{agent_role}' 未配置模型。请在「设置→Pipeline 配置」中分配。"
            )
        inst = self._get_provider(provider, model, prov_cfg)
        async for token in inst.generate_stream(
            messages, temperature=temperature, max_tokens=max_tokens, **kw,
        ):
            yield token


# ────────────────────────────────────────────────────────────────────
# Public factory
# ────────────────────────────────────────────────────────────────────

def build_router(provider: str = "", model: str = "") -> SimpleRouter:
    """Build a router from user settings.

    Explicit (provider, model) wins as the fallback. Otherwise we look
    through the pipeline config for the first configured role, then
    scan enabled providers, then probe Ollama directly, then fall back
    to mock if test mode is on. Raises ValueError if nothing works.
    """
    user_settings = get_user_settings()
    providers_cfg = user_settings.get("providers", {})
    pipeline = user_settings.get("pipeline", {})

    fb_provider = provider
    fb_model = model

    if not fb_provider or not fb_model:
        for role_key in (
            "scene_director", "editor_stylist", "editor_writer",
            "actor_default", "evaluator",
        ):
            role_cfg = pipeline.get(role_key, {})
            p, m = role_cfg.get("provider", ""), role_cfg.get("model", "")
            if p and m:
                fb_provider = fb_provider or p
                fb_model = fb_model or m
                break

    if not fb_provider or not fb_model:
        for pname, pcfg in providers_cfg.items():
            if pcfg.get("enabled") and pcfg.get("models"):
                fb_provider = fb_provider or pname
                fb_model = fb_model or pcfg["models"][0]
                break

    if not fb_provider or not fb_model:
        # Auto-detect Ollama as last resort
        ollama_cfg = providers_cfg.get("ollama", {})
        try:
            import httpx
            base = ollama_cfg.get("base_url", "http://localhost:11434")
            resp = httpx.get(f"{base}/api/tags", timeout=5)
            if resp.status_code == 200:
                ollama_models = [m["name"] for m in resp.json().get("models", [])]
                if ollama_models:
                    fb_provider = "ollama"
                    fb_model = ollama_models[0]
        except Exception:
            pass

    if not fb_model:
        if os.environ.get("WN_TEST_MODE") == "1":
            fb_provider = "mock"
            fb_model = "mock-test-v1"
        else:
            raise ValueError(
                "未找到可用的 AI 模型。请在「设置」页面中启用一个模型供应商并配置模型。"
            )

    return SimpleRouter(user_settings, fb_provider, fb_model)
