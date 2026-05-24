"""
Publishing-platform creative profiles.

A project's target platform (起点 / 番茄 / 晋江 / ...) implies concrete
creative norms — chapter length, pacing, opening strategy — that the
generation prompts should respect. ``get_platform_directive()`` turns a
stored (free-text) platform string into an injectable Chinese directive.

Mirrors ``ui/frontend/src/utils/platforms.ts`` — kept as two thin files so
the frontend has the ``<select>`` list and the backend has the directive
text without an API round-trip.
"""
from __future__ import annotations

PLATFORM_PROFILES: list[dict] = [
    {
        "id": "qidian",
        "label": "起点中文网",
        "aliases": ["起点", "qidian", "起点中文"],
        "chapter_word_target": (2500, 4000),
        "pacing": "中速",
        "directive": (
            "起点读者接受较长章节与世界观铺陈：单章建议 2500-4000 字，节奏中速；"
            "重视设定深度与文笔，黄金三章内建立核心冲突即可，允许适度铺垫。"
        ),
    },
    {
        "id": "fanqie",
        "label": "番茄小说",
        "aliases": ["番茄", "fanqie", "tomato"],
        "chapter_word_target": (1500, 2500),
        "pacing": "快",
        "directive": (
            "番茄读者偏好短章快节奏：单章建议 1500-2500 字，情节推进密集；"
            "爽点前置、少铺垫多冲突，首章即抛出爽点或核心钩子强开局。"
        ),
    },
    {
        "id": "jjwxc",
        "label": "晋江文学",
        "aliases": ["晋江", "jjwxc", "晋江文学城"],
        "chapter_word_target": (2000, 3500),
        "pacing": "中速",
        "directive": (
            "晋江读者重情感与人物刻画：单章建议 2000-3500 字，节奏中速；"
            "细腻描写与情绪铺陈优先，重视人物情感线的建立。"
        ),
    },
    {
        "id": "other",
        "label": "其他",
        "aliases": ["other"],
        "chapter_word_target": (2000, 3000),
        "pacing": "中速",
        "directive": "",
    },
]


def _norm(s: str | None) -> str:
    return (s or "").strip().lower()


def resolve_platform(platform: str | None) -> dict | None:
    """Fuzzy-resolve a free-text platform string to a profile dict."""
    norm = _norm(platform)
    if not norm:
        return None
    for p in PLATFORM_PROFILES:
        if _norm(p["label"]) == norm or p["id"] == norm:
            return p
        if any(_norm(a) == norm for a in p["aliases"]):
            return p
    for p in PLATFORM_PROFILES:
        if p["id"] == "other":
            continue
        if _norm(p["label"]) and _norm(p["label"]) in norm:
            return p
        if any(_norm(a) and _norm(a) in norm for a in p["aliases"]):
            return p
    return None


def get_platform_directive(platform: str | None) -> str:
    """Return the injectable Chinese directive for a platform, or "" if
    the platform is unknown / unset / "其他" (no constraint)."""
    p = resolve_platform(platform)
    if not p:
        return ""
    return p.get("directive", "") or ""
