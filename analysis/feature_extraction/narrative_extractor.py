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
                head = (chapters[j - 1].get("content") or "")[:200] if j - 1 < len(chapters) else ""
                tm = ""
                dm = _DATE_HINT_PAT.search(head)
                if dm:
                    tm = dm.group(1).strip()
                events.append({
                    "subject": "正文",
                    "category": f"第{j}章",
                    "name": t[:30] or f"第{j}章节点",
                    "description": "AI 未返回该章内容，请点击事件右侧编辑按钮手动补充。",
                    "hidden": "",
                    "time_marker": tm or f"第 {j} 章",
                })
            # shuangdian falling in this segment
            for sd in shuangdian:
                ch = sd.get("chapter")
                if isinstance(ch, int) and seg_start <= ch <= seg_end:
                    events.append({
                        "subject": "结构",
                        "category": _SHUANGDIAN_LABELS.get(sd.get("type", ""), str(sd.get("type") or "事件")),
                        "name": f"第{ch}章爽点",
                        "description": "AI 未返回该章内容，请点击事件右侧编辑按钮手动补充。",
                        "hidden": "",
                        "time_marker": f"第 {ch} 章",
                    })
            # climax marker
            for ch in sorted(climaxes):
                if seg_start <= ch <= seg_end:
                    events.append({
                        "subject": "结构",
                        "category": "高潮",
                        "name": f"第{ch}章张力峰值",
                        "description": "AI 未返回该章内容，请点击事件右侧编辑按钮手动补充。",
                        "hidden": "",
                        "time_marker": f"第 {ch} 章",
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

# ════════════════════════════════════════════════════════════════════
# Rhythm v2 — consolidated narrative + rhythm extraction
# ════════════════════════════════════════════════════════════════════

# Keyword buckets for multi-label chapter type classification
_TYPE_BATTLE   = set("杀血战斗刀剑拳枪箭招式爆崩裂攻破败逃")
_TYPE_DAILY    = set("修炼打坐冥想吃饭聊天休息散步沉思微笑点头睡觉")
_TYPE_PROTAG_HINTS = ("回忆", "心里想", "暗自", "独自")
# Match a date hint anywhere in the chapter
_TURNING_RATIO = 0.35  # info_density swing > this triggers 转折

CHAPTER_TYPES_ENUM = (
    "日常", "战斗", "高潮", "角色个人回",
    "主线事件", "支线事件", "伏笔铺垫", "收束",
    "转折", "其他",
)


def _info_density(text: str) -> float:
    """Same shape as _tension but renamed for clarity."""
    return _tension(text)


def _classify_chapter_types(
    *, text: str, chapter_index: int, total_chapters: int,
    info_density: float, avg_density: float, prev_density: float | None,
    is_climax: bool, has_shuangdian: bool,
) -> list[str]:
    """Multi-label chapter classification — returns ALL matching tags
    (at least 1; falls back to ['其他']). See plan B3 for the rules."""
    types: list[str] = []
    n = max(len(text), 1)
    battle_hits = sum(1 for c in text if c in _TYPE_BATTLE)
    daily_hits  = sum(1 for c in text if c in _TYPE_DAILY)
    battle_density = battle_hits / (n / 100)
    daily_density  = daily_hits  / (n / 100)

    if is_climax or info_density > avg_density * 1.4:
        types.append("高潮")
    if battle_density > 2:
        types.append("战斗")
    if daily_density > 1.5 and battle_density < 1:
        types.append("日常")
    if any(h in text for h in _TYPE_PROTAG_HINTS) and len(text) > 1000:
        types.append("角色个人回")
    if has_shuangdian or is_climax:
        if "主线事件" not in types:
            types.append("主线事件")
    # 支线: chapter explicitly mentions secondary locations/cast — heuristic
    # left intentionally loose; the AI extractor refines this.
    # 伏笔铺垫: hook hits at the end of the chapter with low density
    tail = text[-300:] if len(text) > 300 else text
    hook_hits = sum(1 for p in _HOOK_PATS if p.search(tail))
    if hook_hits and info_density < avg_density:
        types.append("伏笔铺垫")
    if total_chapters > 0 and chapter_index >= int(total_chapters * 0.9):
        types.append("收束")
    if prev_density is not None and abs(info_density - prev_density) > _TURNING_RATIO:
        types.append("转折")

    if not types:
        types.append("其他")
    # dedup while preserving order
    seen: set[str] = set()
    out: list[str] = []
    for t in types:
        if t not in seen:
            seen.add(t); out.append(t)
    return out


def _extract_hooks(text: str) -> list[dict]:
    """Detect hook sentences with position labels. Returns [{position, content}]."""
    if not text:
        return []
    hooks: list[dict] = []
    L = len(text)
    head = text[:300]
    tail = text[-300:] if L > 300 else ""
    middle = text[300:-300] if L > 600 else ""

    def _scan(region: str, label: str):
        for p in _HOOK_PATS:
            for m in p.finditer(region):
                content = m.group(0).strip()
                if not content:
                    continue
                # Cap content length and dedup against existing
                snippet = content[:80]
                if any(h["content"] == snippet for h in hooks):
                    continue
                hooks.append({"position": label, "content": snippet})

    _scan(head, "章首")
    _scan(middle, "段中")
    _scan(tail, "章末")
    return hooks


def _chapter_summary(ch: dict) -> str:
    """Best-effort 1-line summary for NLP fallback — uses title; AI overrides."""
    title = (ch.get("title") or "").strip()
    if title:
        return title[:60]
    body = (ch.get("content") or "").strip()
    if not body:
        return ""
    # First sentence, trimmed
    import re as _re
    first = _re.split(r"[。！？\n]", body, maxsplit=1)[0]
    return (first or body)[:60]


def extract_rhythm_v2(chapters: list[dict]) -> dict[str, Any]:
    """Unified narrative + rhythm extractor. Returns RhythmJson shape.

    Replaces extract_narrative + extract_rhythm. The old functions are
    retained for any callers that haven't migrated yet.
    """
    if not chapters:
        return {
            "coverage": {"chapters": 0, "chars": 0},
            "opening_pattern": "character_intro",
            "climax_positions": [],
            "shuangdian": [],
            "chapter_features": [],
            "info_density_curve": [],
            "pacing_segments": [],
        }

    densities = [_info_density(ch.get("content", "")) for ch in chapters]
    avg_density = sum(densities) / max(len(densities), 1)
    total = len(chapters)

    # opening pattern (reused from extract_narrative logic)
    first = chapters[0].get("content", "")[:500]
    opening = "character_intro"
    for name, pat in _OPENING.items():
        if pat.search(first):
            opening = name
            break

    # climaxes — local peaks
    climaxes: list[int] = []
    for i in range(1, len(densities) - 1):
        if (densities[i] > densities[i-1] and densities[i] > densities[i+1]
                and densities[i] > avg_density * 1.4):
            climaxes.append(i + 1)
    climax_set = set(climaxes)

    # shuangdian
    sd_positions: list[dict] = []
    for i, ch in enumerate(chapters):
        c = ch.get("content", "")
        for cat, pats in _SHUANGDIAN.items():
            if any(p.search(c) for p in pats):
                sd_positions.append({"chapter": i + 1, "type": cat})
                break
    sd_chapters = {sd["chapter"] for sd in sd_positions}

    # per-chapter features (multi-label types)
    chapter_features: list[dict] = []
    prev_d: float | None = None
    for i, ch in enumerate(chapters):
        content = ch.get("content", "")
        d = densities[i]
        chap_no = i + 1
        types = _classify_chapter_types(
            text=content, chapter_index=chap_no, total_chapters=total,
            info_density=d, avg_density=avg_density, prev_density=prev_d,
            is_climax=(chap_no in climax_set),
            has_shuangdian=(chap_no in sd_chapters),
        )
        chapter_features.append({
            "chapter": chap_no,
            "types": types,
            "info_density": d,
            "summary": _chapter_summary(ch),
            "hooks": _extract_hooks(content),
        })
        prev_d = d

    # pacing segments (same algorithm, now keyed off info_density)
    def _pace(t: float) -> str:
        return "fast" if t > 0.6 else ("slow" if t < 0.3 else "medium")
    pacing_segments: list[dict] = []
    if densities:
        cur = _pace(densities[0])
        start = 0
        for i in range(1, len(densities)):
            p = _pace(densities[i])
            if p != cur:
                pacing_segments.append({
                    "start": start + 1, "end": i, "pacing": cur,
                    "avg_info_density": round(
                        sum(densities[start:i]) / max(i - start, 1), 3),
                })
                cur = p
                start = i
        pacing_segments.append({
            "start": start + 1, "end": len(densities), "pacing": cur,
            "avg_info_density": round(
                sum(densities[start:]) / max(len(densities) - start, 1), 3),
        })

    total_chars = sum(len(ch.get("content") or "") for ch in chapters)
    return {
        "coverage": {"chapters": total, "chars": total_chars},
        "opening_pattern": opening,
        "climax_positions": climaxes,
        "shuangdian": sd_positions,
        "chapter_features": chapter_features,
        "info_density_curve": densities,
        "pacing_segments": pacing_segments,
    }
