"""Static whitelist of (provider, model-prefix) pairs that support
NATIVE skill mounting (spec Skill类·机制6/机制8).

Native mode: the model's own skill / tool mechanism (e.g. Claude's
skill 机制) loads full skill content on demand — the prompt only
carries a lightweight manifest and the skills loader's character
budget is NOT consumed by skill bodies.

Injection mode (everything else): recalled skills are compressed to
actionable rules (机制7) and injected into the prompt within the
skills loader budget.

Same hardcoded-registry pattern as web_search_capabilities — the
capable set is small and slow-moving; ModelRouter reads this flag to
pick the assembly mode.
"""
from __future__ import annotations

NATIVE_SKILL_CAPABLE: list[tuple[str, str]] = [
    # Anthropic — Claude skill mechanism
    ("anthropic", "claude-"),
]


def supports_native_skills(provider: str, model: str) -> bool:
    """True iff (provider, model) supports native skill mounting.
    Prefix match so versioned variants pass."""
    if not provider or not model:
        return False
    p = provider.strip().lower()
    m = model.strip().lower()
    for cap_p, cap_m_prefix in NATIVE_SKILL_CAPABLE:
        if cap_p == p and m.startswith(cap_m_prefix.lower()):
            return True
    return False
