"""Static whitelist of (provider, model-prefix) pairs that support
LLM-driven web search via a hosted/built-in tool.

This is intentionally hardcoded rather than queried at runtime — the SDK
methods needed to detect the capability vary per provider, and the set
of capable models is small and slow-moving. Update when new ones land.
"""
from __future__ import annotations

WEB_SEARCH_CAPABLE: list[tuple[str, str]] = [
    # Anthropic — web_search_20250305 hosted tool
    ("anthropic", "claude-3-7-sonnet"),
    ("anthropic", "claude-sonnet-4"),
    ("anthropic", "claude-opus-4"),
    # OpenAI — web_search hosted tool (gpt-4o family + gpt-4-turbo)
    ("openai", "gpt-4o"),
    ("openai", "gpt-4-turbo"),
    # Gemini grounding and Grok built-in are deferred until provider
    # implementations land.
]


def supports_web_search(provider: str, model: str) -> bool:
    """Return True iff (provider, model-name) is in the capability set.
    Matches by model-name *prefix* so versioned variants pass
    (e.g. ``gpt-4o-2024-11-20`` matches ``gpt-4o``)."""
    if not provider or not model:
        return False
    p = provider.strip().lower()
    m = model.strip().lower()
    for cap_p, cap_m_prefix in WEB_SEARCH_CAPABLE:
        if cap_p == p and m.startswith(cap_m_prefix.lower()):
            return True
    return False


def describe(provider: str, model: str) -> str:
    """Human-readable rationale for the UI tooltip."""
    if not provider or not model:
        return "未配置 reference_web_search 角色或模型"
    if supports_web_search(provider, model):
        return f"{provider}/{model} 支持联网搜索"
    return f"{provider}/{model} 暂未接入联网搜索"
