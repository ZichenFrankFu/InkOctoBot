"""
Shuangdian (爽点) template extraction.

Extends the ``_SHUANGDIAN`` patterns from ``narrative_extractor.py`` with
richer type taxonomy and structured context extraction (前因→触发→高潮→反应).
"""
from __future__ import annotations

import re
from typing import Any

# ── Extended shuangdian taxonomy ──
# Maps category → list of compiled patterns

_SHUANGDIAN_PATTERNS: dict[str, list[re.Pattern[str]]] = {
    # Original four from narrative_extractor
    "face_slap": [
        re.compile(r'(?:众人|所有人|全场)[^。]*(?:震惊|呆住|傻眼|目瞪口呆)'),
        re.compile(r'(?:打脸|啪啪|响亮的耳光)'),
    ],
    "power_reveal": [
        re.compile(r'(?:气息|实力|修为)[^。]*(?:暴涨|飙升|骤然提升)'),
        re.compile(r'(?:隐藏的实力|真正的力量|底牌)[^。]*(?:展现|释放|爆发)'),
    ],
    "treasure_gain": [
        re.compile(r'(?:突破|晋级|进阶|升级)[^。]*(?:成功|了)'),
        re.compile(r'(?:获得|得到|拿到)[^。]*(?:宝物|神器|灵药|秘籍|传承)'),
    ],
    "mystery_reveal": [
        re.compile(r'原来[^。]*(?:竟然是|居然是|就是)'),
        re.compile(r'(?:真相|身份|秘密)[^。]*(?:揭开|暴露|大白)'),
    ],
    # New types
    "underdog_win": [
        re.compile(r'(?:以弱胜强|越级挑战|以下犯上|蝼蚁|不自量力)[^。]*(?:胜|赢|败|倒下)'),
        re.compile(r'(?:没有人|谁也没)[^。]*(?:想到|料到)[^。]*(?:竟然|居然)'),
    ],
    "revenge": [
        re.compile(r'(?:终于|今天)[^。]*(?:报仇|雪恨|讨回|了断|清算)'),
        re.compile(r'(?:当年|那时|曾经)[^。]*(?:如今|现在)[^。]*(?:还|偿)'),
    ],
    "recognition": [
        re.compile(r'(?:认出|发现|看出)[^。]*(?:身份|来历|真面目)'),
        re.compile(r'(?:你是|原来你就是|难道你就是)[^。]*(?:！|？)'),
    ],
    "breakthrough": [
        re.compile(r'(?:顿悟|领悟|悟出|悟到)[^。]*(?:了|之道|真谛|奥义)'),
        re.compile(r'(?:瓶颈|桎梏|枷锁)[^。]*(?:突破|打破|冲破)'),
    ],
}

# Context window sizes (characters)
_CONTEXT_BEFORE = 150
_CONTEXT_AFTER = 100


def extract_shuangdian(chapters: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Extract shuangdian instances with structured context.

    Each chapter dict must have keys ``"content"`` and optionally ``"index"``
    or ``"title"``.

    Returns::

        [{"chapter": int, "type": str,
          "context_before": str, "trigger": str,
          "climax": str, "reaction": str}, ...]
    """
    results: list[dict[str, Any]] = []

    for ch in chapters:
        content = ch.get("content", "")
        ch_idx = ch.get("index", 0) + 1

        for cat, pats in _SHUANGDIAN_PATTERNS.items():
            for pat in pats:
                for m in pat.finditer(content):
                    start, end = m.start(), m.end()

                    # Extract surrounding context
                    before_start = max(0, start - _CONTEXT_BEFORE)
                    after_end = min(len(content), end + _CONTEXT_AFTER)

                    context_before = content[before_start:start].strip()
                    climax = m.group().strip()
                    reaction = content[end:after_end].strip()

                    # Trigger: last sentence before match
                    trigger_sents = re.split(r'[。！？…]+', context_before)
                    trigger = trigger_sents[-1].strip() if trigger_sents else ""

                    results.append({
                        "chapter": ch_idx,
                        "type": cat,
                        "context_before": context_before,
                        "trigger": trigger,
                        "climax": climax,
                        "reaction": reaction,
                    })

    return results


def shuangdian_density(chapters: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute shuangdian density and type distribution.

    Returns::

        {"total": int, "per_chapter": float,
         "type_distribution": {type: count, ...},
         "chapter_coverage": float,
         "top_type": str | None}
    """
    items = extract_shuangdian(chapters)
    n_chapters = max(len(chapters), 1)

    dist: dict[str, int] = {}
    chapters_with: set[int] = set()
    for it in items:
        dist[it["type"]] = dist.get(it["type"], 0) + 1
        chapters_with.add(it["chapter"])

    total = sum(dist.values())
    top = max(dist, key=dist.get) if dist else None  # type: ignore[arg-type]

    return {
        "total": total,
        "per_chapter": round(total / n_chapters, 2),
        "type_distribution": dist,
        "chapter_coverage": round(len(chapters_with) / n_chapters, 3),
        "top_type": top,
    }
