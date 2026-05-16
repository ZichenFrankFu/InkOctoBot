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


_SHUANGDIAN_LABELS = {
    "face_slap": "打脸/反转",
    "power_reveal": "实力展现",
    "treasure_gain": "突破/晋级",
    "mystery_reveal": "谜底揭开",
}
_OPENING_LABELS = {
    "in_medias_res": "高潮开局",
    "dialogue_open": "对话开局",
    "worldbuilding": "世界观铺陈",
    "character_intro": "人物登场",
}


_VOLUME_PAT = re.compile(
    r"^[　\s]*第[零一二三四五六七八九十百千万\d]+卷[\s：:　]*(.*)",
    re.MULTILINE,
)
_DATE_HINT_PAT = re.compile(
    r"(\d{2,4}\s*年(?:\s*\d{1,2}\s*月(?:[上中下]旬)?)?|"
    r"[零一二三四五六七八九十百千]+\s*年代|"
    r"远古|上古|纪元前|纪元后|\d+\s*周年)"
)


def _extract_time_hint(title: str, content_head: str = "") -> str:
    """Try to find a time-ish label in chapter title or first sentence."""
    for src in (title, content_head):
        if not src:
            continue
        m = _DATE_HINT_PAT.search(src)
        if m:
            return m.group(1).strip()
    return ""


def extract_plot_outline(chapters: list[dict],
                          narrative: dict | None = None) -> dict[str, Any]:
    """Build a chronicle-format plot outline skeleton.

    Produces { logline, epochs: [{title, periods: [{time, events: [...]}]}] }.
    Each event uses the strict 编年史 schema (subject·category·name·description·hidden).

    This is a *skeleton* extractor — given the strictness of the format
    (no dialogue/psychology/scene-detail), the generated content is a
    starting point and the user is expected to fill in details. Auto rules:

    - Group consecutive chapters into periods, splitting at climaxes or
      every ~K chapters, whichever comes first.
    - Each period gets one placeholder event using chapter titles.
    - If chapter title contains a date-like token (年/月/旬), use it as
      the period's time label; otherwise fall back to a chapter-range label
      (which the user is expected to rewrite per chronicle rules).
    - Climaxes / shuangdian produce extra "concept" events under the
      period they fall in.
    - If text contains "第X卷" volume markers, split into epochs by volume.
    """
    if not chapters:
        return {"logline": "", "epochs": []}

    titles = [
        (ch.get("title") or f"第{i+1}章").strip()
        for i, ch in enumerate(chapters)
    ]
    total = len(chapters)

    if narrative is None:
        narrative = extract_narrative(chapters)

    climaxes: set[int] = set(narrative.get("climax_positions") or [])
    shuangdian: list[dict] = list(narrative.get("shuangdian") or [])

    # Detect volume boundaries from chapter titles (some books prefix with 第X卷)
    vol_boundaries: list[tuple[int, str]] = []  # (chapter index 1-based, vol title)
    for i, t in enumerate(titles):
        if _VOLUME_PAT.match(t):
            vol_boundaries.append((i + 1, t[:40]))

    # Build periods by walking chapters
    K_PERIOD = max(3, total // 8)  # roughly 8 periods if no other signal

    def _build_periods(start_idx: int, end_idx: int) -> list[dict]:
        """start/end are 1-based inclusive chapter numbers."""
        periods: list[dict] = []
        cur_start = start_idx
        for ci in range(start_idx, end_idx + 1):
            should_cut = (
                ci == end_idx
                or (ci - cur_start + 1) >= K_PERIOD
                or ci in climaxes
            )
            if not should_cut:
                continue
            seg_start = cur_start
            seg_end = ci
            # time label
            time_hint = ""
            if seg_start - 1 < len(chapters):
                head = (chapters[seg_start - 1].get("content") or "")[:120]
                time_hint = _extract_time_hint(titles[seg_start - 1], head)
            time_label = time_hint or (
                f"第 {seg_start} – {seg_end} 章"
                if seg_end > seg_start else f"第 {seg_start} 章"
            )

            # events: one per (small) chapter, capped; plus shuangdian
            events: list[dict] = []
            chap_count = seg_end - seg_start + 1
            sample_step = max(1, chap_count // 3)  # ≤3 placeholder events per period
            for j in range(seg_start, seg_end + 1, sample_step):
                t = titles[j - 1] if j - 1 < len(titles) else ""
                events.append({
                    "subject": "正文",
                    "category": f"第{j}章",
                    "name": t[:30] or f"第{j}章节点",
                    "description": "（需作者依编年史规则改写：客观叙述本章发生的关键事实，避免对话/心理/场景细节。）",
                    "hidden": "",
                })
            # shuangdian falling in this segment
            for sd in shuangdian:
                ch = sd.get("chapter")
                if isinstance(ch, int) and seg_start <= ch <= seg_end:
                    events.append({
                        "subject": "结构",
                        "category": _SHUANGDIAN_LABELS.get(sd.get("type", ""), str(sd.get("type") or "事件")),
                        "name": f"第{ch}章爽点",
                        "description": "（自动检测的爽点节奏，需作者改写为该章的客观关键事实。）",
                        "hidden": "",
                    })
            # climax marker
            for ch in sorted(climaxes):
                if seg_start <= ch <= seg_end:
                    events.append({
                        "subject": "结构",
                        "category": "高潮",
                        "name": f"第{ch}章张力峰值",
                        "description": "（自动检测的张力峰值，需作者改写为该章的客观关键事实。）",
                        "hidden": "",
                    })
            periods.append({"time": time_label, "events": events})
            cur_start = ci + 1
        return periods

    epochs: list[dict] = []
    if vol_boundaries:
        # Volume-based epochs
        boundaries = vol_boundaries + [(total + 1, "")]
        for i in range(len(boundaries) - 1):
            v_start, v_title = boundaries[i]
            v_end = boundaries[i + 1][0] - 1
            if v_start > v_end:
                continue
            epochs.append({
                "title": v_title or f"第{i+1}卷",
                "periods": _build_periods(v_start, v_end),
            })
    else:
        # Single unnamed epoch
        epochs.append({
            "title": "",
            "periods": _build_periods(1, total),
        })

    return {
        "logline": "",
        "epochs": epochs,
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