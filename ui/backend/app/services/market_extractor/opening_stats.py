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

# Multi-char everyday words. Without this blocklist 高频词 / 生造词
# degrade into 自己 / 一个 / 没有 / 什么 — high-frequency but semantically
# empty function words. These are the words to NEVER surface as a content
# keyword or a coined term, regardless of how often they appear.
_COMMON_WORDS: frozenset[str] = frozenset("""
自己 一个 没有 什么 这个 那个 怎么 知道 现在 时候 这样 那样 起来 出来
可能 已经 还是 因为 所以 但是 如果 这些 那些 我们 你们 他们 她们 它们
一样 一直 一定 不过 不能 不是 这么 那么 突然 似乎 仿佛 觉得 看着 看到
听到 想到 感觉 应该 只是 就是 还有 然后 这时 此时 这里 那里 一些 有些
东西 事情 时间 地方 样子 一点 一切 所有 之后 之前 当然 也许 或许 终于
竟然 居然 顿时 立刻 立即 马上 刚才 刚刚 周围 旁边 身边 心里 心中 眼中
眼前 面前 身上 身后 头上 手中 不会 不要 不想 不敢 没想 想要 一声
两个 三个 几个 那种 这种 一种 自然 似的 一边 那边 这边 别人 大家 整个
有人 没人 什么样 不停 不由 不禁 不住 一时 半天 一阵 片刻 随即 接着 于是
而且 然而 不仅 甚至 反正 毕竟 何况 难道 无非 只有 只要 即使 哪怕 除非
什么的 怎么样 为什么 怎么办 一会儿 这会儿 那会儿
""".split())

# Generic narration verbs/adverbs that jieba tags as nouns/verbs but carry
# no platform-distinguishing signal.
_GENERIC_CONTENT: frozenset[str] = frozenset("""
说道 问道 喊道 笑道 答道 开口 摇头 点头 抬头 低头 转身 伸手 走来 走去
站着 坐着 躺着 看了 听了 想了 笑了 叹了 时候 样子 声音 目光 眼睛 脸上
""".split())


def _is_common(w: str) -> bool:
    return w in _COMMON_WORDS or w in _GENERIC_CONTENT


# jieba POS tags worth keeping as content keywords: nouns, proper nouns
# (person/place/org/other), idioms. Everything else (pronouns, adverbs,
# particles, generic verbs) is dropped.
_KEEP_POS_PREFIX = ("n", "nr", "ns", "nt", "nz", "nl", "ng")


def _top_words(texts: list[str], k: int = 20) -> list[dict[str, Any]]:
    """高频词 — content keywords only. Uses jieba POS to keep nouns /
    proper nouns and drops pronouns, adverbs and everyday function words
    (otherwise 自己 / 一个 / 没有 dominate)."""
    counter: Counter = Counter()
    used_pos = False
    try:
        import jieba.posseg as _pseg
        for t in texts:
            for tok in _pseg.cut(t):
                w, flag = tok.word, tok.flag
                if (len(w) >= 2 and re.fullmatch(r"[一-鿿]+", w)
                        and flag.startswith(_KEEP_POS_PREFIX)
                        and not _is_common(w)):
                    counter[w] += 1
        used_pos = True
    except Exception:
        used_pos = False
    if not used_pos or not counter:
        # Fallback: plain jieba.cut with the blocklist, then bigrams.
        try:
            import jieba
            for t in texts:
                for w in jieba.cut(t):
                    if (len(w) >= 2 and re.fullmatch(r"[一-鿿]+", w)
                            and w not in _STOPWORDS and not _is_common(w)):
                        counter[w] += 1
        except Exception:
            for t in texts:
                chars = re.sub(r"[^一-鿿]", "", t)
                for i in range(len(chars) - 1):
                    bg = chars[i:i + 2]
                    if (bg[0] not in _STOPWORDS and bg[1] not in _STOPWORDS
                            and not _is_common(bg)):
                        counter[bg] += 1
    return [{"word": w, "count": c} for w, c in counter.most_common(k)]


def _neologism_step1(texts: list[str], k: int = 15) -> list[dict[str, Any]]:
    """生造词 Step1 初筛 — 频率 + PMI 凝合度 + 后缀模式 (机制3)。

    复用 neologism_extractor 的 recall 路径，再用通用词 / 成语 / 网络
    流行语 / 古典词词典与常用词黑名单过滤，确保候选是「疑似新造专有
    名词」而非 自己 / 什么 这类常用词。"""
    try:
        from .neologism_extractor import (
            _char_pattern_match, _ngram_pmi, _recall_via_jieba,
        )
    except Exception:
        return []
    # Reference dictionaries of *known* words — anything in them is NOT a
    # coined term.
    known: set[str] = set()
    try:
        from .dictionaries import (
            load_classical_words, load_idioms, load_internet_slang,
        )
        known |= set(load_classical_words())
        known |= set(load_idioms())
        known |= set(load_internet_slang())
    except Exception:
        pass

    # Particles/structural chars that never occur inside a Chinese proper
    # noun — their presence means the candidate is a prose fragment
    # (前的青云宗 / 手里的玄 / 的钟声响) rather than a coined term.
    _HARD_REJECT_CHARS = set("的了着过吗呢吧么哦啊呀嘛")
    # Pronouns / common verb-adverb chars: tolerated singly but a high
    # ratio signals ordinary prose.
    _SOFT_FUNC_CHARS = set("会在是有和就不都也很把被让从向往要去到我你他她它"
                           "们这那怎什没看说想又再还只能可如果因所以但必须前后")

    def _looks_coined(term: str) -> bool:
        if len(term) < 2 or len(term) > 6:
            return False
        if not re.fullmatch(r"[一-鿿]+", term):
            return False
        if _is_common(term) or term in known:
            return False
        if any(ch in _HARD_REJECT_CHARS for ch in term):
            return False
        # Contains an everyday word as a chunk (自己怎么会 / 要进去吗).
        if any(cw in term for cw in _COMMON_WORDS):
            return False
        soft = sum(1 for ch in term if ch in _SOFT_FUNC_CHARS) / len(term)
        if soft > 0.34:
            return False
        if all(ch in _STOPWORDS for ch in term):
            return False
        return True

    cands: Counter = Counter()
    joined = "\n".join(texts)
    pool: set[str] = set()
    pool |= _ngram_pmi(joined)
    pool |= _char_pattern_match(joined)
    pool |= {w for w in _recall_via_jieba(joined) if len(w) >= 3}
    for term in pool:
        if not _looks_coined(term):
            continue
        n = joined.count(term)
        if n >= 2:
            cands[term] = n

    # Substring dedup: drop a fragment when a longer candidate contains it
    # and occurs about as often (李慕白 present → drop 慕白 / 李慕; 宗门大比
    # present → drop 宗门大 / 大比). Keeps the longest coherent coined term.
    ordered = sorted(cands, key=lambda t: (-len(t), -cands[t]))
    kept: list[str] = []
    for term in ordered:
        covered = any(
            term != longer and term in longer and cands[longer] >= cands[term] * 0.6
            for longer in kept
        )
        if not covered:
            kept.append(term)
    result = sorted(kept, key=lambda t: -cands[t])[:k]
    return [{"term": t, "count": cands[t]} for t in result]


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
