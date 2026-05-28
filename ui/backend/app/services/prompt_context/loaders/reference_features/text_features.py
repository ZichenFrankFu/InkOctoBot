"""text_features feature — borrows style fingerprint / rhythm from a reference work."""
from __future__ import annotations

from typing import Any

from ._common import clip_feature, coerce_dict, score_against_outline


def _summarize(rhythm: dict, style: dict) -> str:
    bits: list[str] = []
    # Rhythm: per-segment beat density / scene tempo summary.
    if rhythm:
        for k, v in list(rhythm.items())[:6]:
            text = str(v).strip()
            if text:
                bits.append(f"{k}：{text[:80]}")
    # Style fingerprint: avg sentence length, short-sentence ratio, etc.
    if style:
        try:
            asl = style.get("avg_sentence_length")
            ssr = style.get("short_sentence_ratio")
            dr  = style.get("dialogue_ratio")
            dd  = style.get("description_density")
            if asl is not None:
                bits.append(f"平均句长 ~{asl}")
            if ssr is not None:
                bits.append(f"短句率 {float(ssr):.0%}")
            if dr is not None:
                bits.append(f"对话占比 {float(dr):.0%}")
            if dd is not None:
                bits.append(f"描写密度 {float(dd):.0%}")
        except (TypeError, ValueError):
            pass
    return "；".join(bits)


def load_for_work(work: dict, **_: Any) -> str:
    rhythm = coerce_dict(work.get("rhythm_json"))
    style = coerce_dict(work.get("style_fingerprint_json"))
    return clip_feature(_summarize(rhythm, style))


def score_by_outline(work: dict, outline_vec: list[float]) -> float:
    # Style is mostly outline-independent, so we don't expect strong
    # signal here; embed the textual summary anyway so cross-work
    # ranking is stable.
    rhythm = coerce_dict(work.get("rhythm_json"))
    style = coerce_dict(work.get("style_fingerprint_json"))
    return score_against_outline(_summarize(rhythm, style), outline_vec)
