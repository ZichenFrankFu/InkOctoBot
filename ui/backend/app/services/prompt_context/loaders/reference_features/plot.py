"""plot feature — borrows plot structure / arcs from a reference work."""
from __future__ import annotations

from typing import Any

from ._common import clip_feature, coerce_dict, coerce_list, score_against_outline


def _summarize(plot_outline: dict | list) -> str:
    if isinstance(plot_outline, dict):
        beats = (
            plot_outline.get("beats")
            or plot_outline.get("acts")
            or plot_outline.get("outline")
            or []
        )
        if isinstance(beats, list):
            lines = []
            for b in beats[:6]:
                if isinstance(b, dict):
                    lines.append((b.get("title") or b.get("name") or "")
                                 + "：" + (b.get("description") or b.get("summary") or "")[:80])
                else:
                    lines.append(str(b)[:120])
            return "；".join(s for s in lines if s.strip())
        # Free-form dict → join values
        return "；".join(str(v)[:120] for v in plot_outline.values() if v)
    if isinstance(plot_outline, list):
        return "；".join(str(x)[:120] for x in plot_outline[:6])
    return ""


def load_for_work(work: dict, **_: Any) -> str:
    raw = work.get("plot_outline_json")
    obj = coerce_dict(raw)
    if not obj:
        obj = coerce_list(raw) or {}
    out = _summarize(obj)
    return clip_feature(out)


def score_by_outline(work: dict, outline_vec: list[float]) -> float:
    raw = work.get("plot_outline_json")
    obj = coerce_dict(raw) or coerce_list(raw)
    text = _summarize(obj if isinstance(obj, (dict, list)) else {})
    return score_against_outline(text, outline_vec)
