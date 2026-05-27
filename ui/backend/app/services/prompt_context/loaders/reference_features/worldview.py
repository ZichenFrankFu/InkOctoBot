"""worldview feature — borrows world / setting design from a reference work."""
from __future__ import annotations

from typing import Any

from ._common import clip_feature, coerce_dict, coerce_list, score_against_outline


def _summarize(settings: dict | list) -> str:
    if isinstance(settings, dict):
        bits: list[str] = []
        for label, value in settings.items():
            if isinstance(value, (dict, list)):
                value = (
                    "；".join(str(v) for v in value[:4])
                    if isinstance(value, list)
                    else "；".join(f"{k}: {v}" for k, v in list(value.items())[:4])
                )
            text = str(value or "").strip()
            if not text:
                continue
            bits.append(f"{label}：{text[:120]}")
        return "；".join(bits[:6])
    if isinstance(settings, list):
        return "；".join(str(x)[:120] for x in settings[:6])
    return ""


def load_for_work(work: dict, **_: Any) -> str:
    raw = work.get("settings_json")
    obj = coerce_dict(raw) or coerce_list(raw) or {}
    return clip_feature(_summarize(obj))


def score_by_outline(work: dict, outline_vec: list[float]) -> float:
    raw = work.get("settings_json")
    obj = coerce_dict(raw) or coerce_list(raw) or {}
    return score_against_outline(_summarize(obj), outline_vec)
