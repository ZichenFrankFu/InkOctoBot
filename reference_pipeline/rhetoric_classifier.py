"""
Rhetorical device classifier.

Classifies sentences by rhetorical device type using rule-based pattern
matching.  Extends the simile / metaphor patterns already present in
``preprocessing/style_extractor.py`` and ``nlp_stats.py``.
"""
from __future__ import annotations

import re
from typing import Any

# ── Pattern library ──
# Each entry: (type_name, compiled regex, base confidence)

_PATTERNS: list[tuple[str, re.Pattern[str], float]] = [
    # 比喻 (simile)
    ("simile", re.compile(
        r'(?:如同|宛如|好似|像是|犹如|仿佛|好像|恰似|一如)'
        r'[^，。！？]{2,25}(?:般|一样|似的|一般)?'
    ), 0.85),
    # 隐喻 (metaphor)
    ("metaphor", re.compile(
        r'(?:是|化作|变成|化为|成了)[^，。！？]{2,20}的[^。！？]{2,12}'
    ), 0.70),
    # 排比 (parallelism) — three or more structurally similar clauses
    ("parallelism", re.compile(
        r'([^，。！？]{3,12})[，；]'
        r'([^，。！？]{3,12})[，；]'
        r'([^，。！？]{3,12})'
    ), 0.65),
    # 反问 (rhetorical question)
    ("rhetorical_question", re.compile(
        r'(?:难道|岂|莫非|何尝|怎能|怎么可能|哪里|何必)[^？]*？'
    ), 0.90),
    # 夸张 (hyperbole)
    ("hyperbole", re.compile(
        r'(?:天崩地裂|惊天动地|翻江倒海|气吞山河|移山倒海'
        r'|遮天蔽日|铺天盖地|一眼万年|千万倍|亿万|无穷无尽'
        r'|无边无际|震碎|轰碎|整个天空|整片大地|方圆万里'
        r'|万丈|千丈|百丈|足以毁灭)'
    ), 0.80),
    # 拟人 (personification)
    ("personification", re.compile(
        r'(?:风|月|云|雨|山|河|花|草|树|石|雷|雪|星|日|天)'
        r'[^，。！？]{0,6}'
        r'(?:低语|沉默|怒吼|哭泣|微笑|叹息|呢喃|歌唱|跳舞|沉睡|苏醒|呼唤)'
    ), 0.75),
    # 对比 (contrast / antithesis)
    ("contrast", re.compile(
        r'(?:不是[^，。！？]{2,15}[，；]而是[^。！？]{2,15}'
        r'|一边[^，。！？]{2,15}[，；]一边[^。！？]{2,15}'
        r'|有的[^，。！？]{2,15}[，；]有的[^。！？]{2,15}'
        r'|表面[^，。！？]{2,15}[，；]实则[^。！？]{2,15})'
    ), 0.75),
    # 通感 (synesthesia) — mixing senses
    ("synesthesia", re.compile(
        r'(?:(?:声音|笑声|歌声|嗓音)[^，。！？]{0,8}(?:甜|冷|暖|热|冰|刺|柔|硬|沉)'
        r'|(?:目光|眼神|视线)[^，。！？]{0,8}(?:灼热|冰冷|温暖|刺痛|沉重|柔软)'
        r'|(?:气息|味道|香气)[^，。！？]{0,8}(?:嘹亮|沉闷|明亮|暗淡))'
    ), 0.70),
]

# ── Sentence splitter (shared logic with nlp_stats / style_extractor) ──
_SENT_SPLIT = re.compile(r'[。！？…]+["」』）]?')


def _split_sentences(text: str) -> list[str]:
    """Split text into individual sentences."""
    parts = _SENT_SPLIT.split(text)
    return [s.strip() for s in parts if len(s.strip()) >= 2]


def classify_rhetoric(text: str) -> list[dict[str, Any]]:
    """Classify rhetorical devices found in *text*.

    Matches patterns against the full text (preserving punctuation like ``？``)
    and maps each match back to its enclosing sentence.

    Returns a list of dicts, one per detected device instance::

        [{"sentence": str, "rhetoric_type": str,
          "confidence": float, "pattern_matched": str}, ...]
    """
    if not text:
        return []

    results: list[dict[str, Any]] = []

    for rtype, pat, conf in _PATTERNS:
        for m in pat.finditer(text):
            # Find the enclosing sentence for context
            start = text.rfind("。", 0, m.start())
            start = 0 if start == -1 else start + 1
            end = text.find("。", m.end())
            end = len(text) if end == -1 else end
            sentence = text[start:end].strip()

            results.append({
                "sentence": sentence,
                "rhetoric_type": rtype,
                "confidence": conf,
                "pattern_matched": m.group(),
            })

    return results


def rhetoric_summary(text: str) -> dict[str, Any]:
    """Return an aggregate distribution of rhetorical devices in *text*.

    Output::

        {"total_devices": int,
         "distribution": {type: count, ...},
         "density_per_1k": float,
         "top_type": str | None}
    """
    items = classify_rhetoric(text)
    dist: dict[str, int] = {}
    for it in items:
        rt = it["rhetoric_type"]
        dist[rt] = dist.get(rt, 0) + 1

    total = sum(dist.values())
    text_len = max(len(text), 1)
    top = max(dist, key=dist.get) if dist else None  # type: ignore[arg-type]

    return {
        "total_devices": total,
        "distribution": dist,
        "density_per_1k": round(total / (text_len / 1000), 3),
        "top_type": top,
    }
