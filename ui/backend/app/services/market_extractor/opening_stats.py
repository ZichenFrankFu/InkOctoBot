"""开篇章节 NLP 分析 (spec 2.1.3.2 §1) — shared stats computation.

Pure-Python counting over crawled opening chapters. One implementation
feeds BOTH the 市场特征提取页 的「开篇章节分析」section and the
启动提取 prompt's real-data injection, so the numbers the user sees
and the numbers the LLM reasons over are identical.

Spec dimensions:
- 首章字数 / 章平均字数 / 章中位字数
- 平均句长
- 标点符号密度（逗号、感叹号、问号、省略号、破折号、双引号、方括号
  等，每千字）
- 高频词
- 小说生造词 Step1（频率 + PMI 凝合度初筛 — 复用 neologism recall）
"""
from __future__ import annotations

import re
import statistics
from collections import Counter
from typing import Any

_SENT_SPLIT_RE = re.compile(r"[。！？!?…]+")

# 每类标点的字符集合（计数按出现次数，密度 = 次数 / 千字）。
_PUNCT_CLASSES: dict[str, str] = {
    "逗号":   "，,",
    "感叹号": "！!",
    "问号":   "？?",
    "省略号": "…",      # "……" counts 2 chars → density still comparable
    "破折号": "—",
    "双引号": "“”\"「」",
    "方括号": "【】[]",
}

_STOPWORDS = set(
    "的了在是我有和就不人都一一个上也很到说要去你会着没有看好自己这她他它"
    "们与而被把那等中下大小多少又再还只如果因为所以可是但是什么这个那个"
)


def _top_words(texts: list[str], k: int = 20) -> list[dict[str, Any]]:
    """高频词 — jieba when available, char-bigram fallback."""
    counter: Counter = Counter()
    try:
        import jieba
        for t in texts:
            for w in jieba.cut(t):
                if len(w) >= 2 and re.match(r"^[一-鿿]+$", w) \
                        and w not in _STOPWORDS:
                    counter[w] += 1
    except Exception:
        for t in texts:
            chars = re.sub(r"[^一-鿿]", "", t)
            for i in range(len(chars) - 1):
                bg = chars[i:i + 2]
                if bg[0] not in _STOPWORDS and bg[1] not in _STOPWORDS:
                    counter[bg] += 1
    return [{"word": w, "count": c} for w, c in counter.most_common(k)]


def _neologism_step1(texts: list[str], k: int = 15) -> list[dict[str, Any]]:
    """生造词 Step1 初筛 — 频率 + PMI 凝合度 + 后缀模式
    (机制3)，复用 neologism_extractor 的 recall 路径。"""
    try:
        from .neologism_extractor import (
            _char_pattern_match, _ngram_pmi, _recall_via_jieba,
        )
    except Exception:
        return []
    cands: Counter = Counter()
    joined = "\n".join(texts)
    pool: set[str] = set()
    pool |= _ngram_pmi(joined)
    pool |= _char_pattern_match(joined)
    pool |= {w for w in _recall_via_jieba(joined) if len(w) >= 3}
    for term in pool:
        n = joined.count(term)
        if n >= 2:
            cands[term] = n
    return [{"term": t, "count": c} for t, c in cands.most_common(k)]


def compute_opening_stats(
    rows: list[dict[str, Any]],
) -> dict[str, Any]:
    """Spec-dimension stats over opening chapters.

    ``rows``: [{"chapter_num": int, "text": str}] — every collected
    opening chapter of the analyzed work set.
    """
    texts = [str(r.get("text") or "") for r in rows if r.get("text")]
    if not texts:
        return {"available": False, "reason": "no chapter text"}

    word_counts = [len(t) for t in texts]
    first_counts = [
        len(str(r["text"]))
        for r in rows
        if r.get("text") and int(r.get("chapter_num") or 0) == 1
    ]

    sentence_lengths: list[float] = []
    punct_counts: Counter = Counter()
    total_chars = 0
    for t in texts:
        total_chars += len(t)
        sents = [s.strip() for s in _SENT_SPLIT_RE.split(t) if s.strip()]
        if sents:
            sentence_lengths.append(sum(len(s) for s in sents) / len(sents))
        for label, chars in _PUNCT_CLASSES.items():
            punct_counts[label] += sum(t.count(ch) for ch in chars)

    punct_density = {
        label: round(punct_counts[label] / total_chars * 1000, 2)
        for label in _PUNCT_CLASSES
    }

    return {
        "available": True,
        "chapters_analyzed": len(texts),
        # 字数维度
        "first_chapter_words_avg": (
            round(statistics.mean(first_counts)) if first_counts else None
        ),
        "chapter_words_avg": round(statistics.mean(word_counts)),
        "chapter_words_median": round(statistics.median(word_counts)),
        # 句长
        "avg_sentence_length": (
            round(statistics.mean(sentence_lengths), 1)
            if sentence_lengths else None
        ),
        # 标点密度（次/千字）
        "punctuation_density_per_1k": punct_density,
        # 高频词 + 生造词 Step1
        "top_words": _top_words(texts),
        "neologism_step1": _neologism_step1(texts),
    }


def render_stats_for_prompt(stats: dict[str, Any]) -> str:
    """Stats → compact Chinese block for prompt injection."""
    if not stats.get("available"):
        return "（暂无开篇章节统计数据）"
    lines = [
        f"- 分析章节数：{stats['chapters_analyzed']}",
        f"- 首章字数（均值）：{stats.get('first_chapter_words_avg') or '—'}",
        f"- 章平均字数：{stats['chapter_words_avg']} · 章中位字数：{stats['chapter_words_median']}",
        f"- 平均句长：{stats.get('avg_sentence_length') or '—'} 字",
    ]
    pd = stats.get("punctuation_density_per_1k") or {}
    if pd:
        lines.append(
            "- 标点密度（次/千字）："
            + "，".join(f"{k} {v}" for k, v in pd.items())
        )
    tw = stats.get("top_words") or []
    if tw:
        lines.append(
            "- 高频词：" + "、".join(
                f"{w['word']}({w['count']})" for w in tw[:15]
            )
        )
    neo = stats.get("neologism_step1") or []
    if neo:
        lines.append(
            "- 生造词Step1候选（频率+凝合度初筛）："
            + "、".join(f"{n['term']}({n['count']})" for n in neo[:12])
        )
    return "\n".join(lines)
