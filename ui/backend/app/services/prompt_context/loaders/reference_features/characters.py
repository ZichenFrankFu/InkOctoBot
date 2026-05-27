"""characters feature — borrows character archetypes from a reference work."""
from __future__ import annotations

from typing import Any

from ._common import clip_feature, coerce_list, score_against_outline


def _summarize(chars: list) -> str:
    """One-line per character: name (role, archetype)."""
    parts: list[str] = []
    for c in chars[:8]:
        if not isinstance(c, dict):
            continue
        name = (c.get("name") or "").strip()
        if not name:
            continue
        role = (c.get("role") or "").strip()
        arch = (c.get("archetype") or c.get("type") or "").strip()
        desc = (c.get("description") or "").strip()
        bits = []
        if role:
            bits.append(role)
        if arch:
            bits.append(arch)
        head = name + (f"（{'、'.join(bits)}）" if bits else "")
        if desc:
            parts.append(f"{head}：{desc[:80]}")
        else:
            parts.append(head)
    return "；".join(parts)


def load_for_work(work: dict, **_: Any) -> str:
    chars = coerce_list(work.get("extracted_characters_json"))
    out = _summarize(chars)
    return clip_feature(out)


def score_by_outline(work: dict, outline_vec: list[float]) -> float:
    """Score = cosine(outline_vec, embed(character summary))."""
    text = _summarize(coerce_list(work.get("extracted_characters_json")))
    return score_against_outline(text, outline_vec)
