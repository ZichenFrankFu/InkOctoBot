"""
Narrative structure extraction.

Provides extract_narrative() and extract_rhythm().
Detects: opening patterns, climax positions, hooks, shuangdian,
chapter function beats, tension curves, pacing segments.
"""
from __future__ import annotations
import re
from typing import Any

_HIGH_TENSION = set(
    "杀死血战斗怒吼爆崩裂轰击攻破败逃危险恐惧震惊暴冲猛狠凶毁灭绝命拼疯焰火雷电剑刀枪箭"
)
_LOW_TENSION = set(
    "修炼打坐冥想散步吃饭休息睡觉平静安宁沉思回忆微笑点头轻声缓缓慢慢平淡"
)
_HOOK_PATS = [
    re.compile(r'(?:突然|忽然|猛然|骤然)[^。！？]*[。！？]$', re.M),
    re.compile(r'[^。！？]*(?:到底|究竟|难道|莫非|竟然)[^。！？]*[？!]$', re.M),
    re.compile(r'(?:然而|但是|可是)[^。]*[。！？]$', re.M),
]
_SHUANGDIAN = {
    "face_slap": [re.compile(r'(?:众人|所有人|全场)[^。]*(?:震惊|呆住|傻眼|目瞪口呆)')],
    "power_reveal": [re.compile(r'(?:气息|实力|修为)[^。]*(?:暴涨|飙升|骤然提升)')],
    "treasure_gain": [re.compile(r'(?:突破|晋级|进阶|升级)[^。]*(?:成功|了)')],
    "mystery_reveal": [re.compile(r'原来[^。]*(?:竟然是|居然是|就是)')],
}
_OPENING = {
    "in_medias_res": re.compile(r'^[^。！？]*(?:杀|战|逃|血|危)'),
    "dialogue_open": re.compile(r'^["「『]'),
    "worldbuilding": re.compile(r'^[^。！？]*(?:大陆|世界|天地|修炼|灵气)'),
    "character_intro": re.compile(r'^[^。！？]*(?:少年|青年|老者|男子|女子|一个人)'),
}


def _tension(text: str) -> float:
    if not text:
        return 0.0
    n = max(len(text), 1)
    hi = sum(1 for c in text if c in _HIGH_TENSION) / (n / 100)
    ex = (text.count("！") + text.count("？")) / n
    sents = re.split(r'[。！？…]+', text)
    short_r = sum(1 for s in sents if 0 < len(s) < 15) / max(len(sents), 1)
    lo = sum(1 for c in text if c in _LOW_TENSION) / (n / 100)
    return round(min(1.0, max(0.0,
        0.35 * min(1, hi / 5) +
        0.25 * min(1, ex * 50) +
        0.20 * short_r +
        0.20 * max(0, 1 - lo / 3)
    )), 3)


def extract_narrative(chapters: list[dict]) -> dict[str, Any]:
    """Full narrative structure analysis."""
    if not chapters:
        return {}

    tensions = [_tension(ch.get("content", "")) for ch in chapters]
    avg_t = sum(tensions) / max(len(tensions), 1)

    # opening pattern
    first = chapters[0].get("content", "")[:500]
    opening = "character_intro"
    for name, pat in _OPENING.items():
        if pat.search(first):
            opening = name
            break

    # climax positions
    climaxes = []
    for i in range(1, len(tensions) - 1):
        if (tensions[i] > tensions[i-1] and tensions[i] > tensions[i+1]
                and tensions[i] > avg_t * 1.4):
            climaxes.append(i + 1)

    # hooks per chapter
    hooks = []
    for ch in chapters:
        tail = ch.get("content", "")[-300:]
        hooks.append(sum(1 for p in _HOOK_PATS if p.search(tail)))
    hook_density = round(sum(hooks) / max(len(chapters), 1), 3)

    # shuangdian
    sd_positions: list[dict] = []
    for i, ch in enumerate(chapters):
        c = ch.get("content", "")
        for cat, pats in _SHUANGDIAN.items():
            if any(p.search(c) for p in pats):
                sd_positions.append({"chapter": i + 1, "type": cat})
                break

    # chapter beats
    beats = []
    for i in range(len(chapters)):
        t = tensions[i]
        prog = i / max(len(chapters) - 1, 1)
        if prog < 0.1:
            func = "intro"
        elif t > avg_t * 1.5:
            func = "climax"
        elif prog > 0.9:
            func = "resolution"
        elif i > 0 and t > tensions[i-1] * 1.15:
            func = "rising"
        else:
            func = "falling" if t < avg_t else "rising"
        beats.append({
            "chapter": i + 1, "function": func,
            "tension": t, "hooks": hooks[i],
        })

    return {
        "opening_pattern": opening,
        "climax_positions": climaxes,
        "hook_density": hook_density,
        "shuangdian": sd_positions,
        "chapter_beats": beats,
    }


def extract_rhythm(chapters: list[dict]) -> dict[str, Any]:
    """Tension curve + pacing segments."""
    tensions = [_tension(ch.get("content", "")) for ch in chapters]

    def _pace(t: float) -> str:
        return "fast" if t > 0.6 else ("slow" if t < 0.3 else "medium")

    segments: list[dict] = []
    if tensions:
        cur = _pace(tensions[0])
        start = 0
        for i in range(1, len(tensions)):
            p = _pace(tensions[i])
            if p != cur:
                segments.append({
                    "start": start + 1, "end": i, "pacing": cur,
                    "avg_tension": round(
                        sum(tensions[start:i]) / max(i - start, 1), 3),
                })
                cur = p
                start = i
        segments.append({
            "start": start + 1, "end": len(tensions), "pacing": cur,
            "avg_tension": round(
                sum(tensions[start:]) / max(len(tensions) - start, 1), 3),
        })

    return {"tension_curve": tensions, "pacing_segments": segments}