"""Platform-name canonicalization — bridges three label families:

- Crawler DB keys (English slugs): ``qidian`` / ``fanqie``
- Project DB stored values (Chinese display labels): ``起点`` / ``番茄``
- Legacy long-form labels: ``起点中文网`` / ``番茄小说``

The platform_directive / market_overview loaders match project values
against stored values purely by string overlap, which fails when one
side uses English (``qidian``) and the other uses Chinese
(``起点中文网``). This module provides a single normalisation function
that maps any known synonym to a canonical Chinese form, so the matcher
sees them as the same platform.

This is **plumbing only** — no style content. The "禁止 hardcoded
platform style" rule applies to the body of ``platform_directive``;
that body is still loaded purely from real extracted features. The
alias map below only translates the platform IDENTIFIER between
crawler-side and project-side conventions.

用户要求暂时只暴露 起点 / 番茄 两个平台, 别的平台等业务需要再回归 —— 别的
synonyms (zongheng / ciweimao / 17K / …) 主动从表里删掉, 防止 UI 里
意外冒出来.
"""
from __future__ import annotations


# Canonical Chinese form (the single label every other loader should
# resolve to). Keys are the lowercased synonyms; values are the canonical
# Chinese label. Add new synonyms here as the crawler / UI evolve.
_PLATFORM_NORMALIZE: dict[str, str] = {
    # 起点
    "qidian":       "起点",
    "起点":         "起点",
    "起点中文":     "起点",
    "起点中文网":   "起点",
    "qidian中文网": "起点",
    # 番茄
    "fanqie":       "番茄",
    "番茄":         "番茄",
    "番茄小说":     "番茄",
    "tomato":       "番茄",
}


def canonicalize_platform(name: str | None) -> str:
    """Map any known synonym to its canonical Chinese form.

    Unknown names pass through unchanged (with surrounding whitespace
    stripped) so brand-new platforms not yet in the table don't
    accidentally collapse to ``""``.
    """
    if not name:
        return ""
    raw = str(name).strip()
    if not raw:
        return ""
    return _PLATFORM_NORMALIZE.get(raw.lower(), raw)


def known_canonicals() -> tuple[str, ...]:
    """All canonical Chinese platform names this module knows about.
    Useful for tests and for the frontend platform picker."""
    seen: list[str] = []
    for v in _PLATFORM_NORMALIZE.values():
        if v not in seen:
            seen.append(v)
    return tuple(seen)
