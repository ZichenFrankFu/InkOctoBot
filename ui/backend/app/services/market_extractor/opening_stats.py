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
# degrade into 自己 / 一个 / 没有 / 什么 / 怎么 / 可以 — high-frequency but
# semantically empty function words. These are NEVER a content keyword or
# a coined term, regardless of how often they appear.
_COMMON_WORDS: frozenset[str] = frozenset("""
自己 一个 没有 什么 这个 那个 怎么 可以 知道 现在 时候 这样 那样 起来 出来
可能 已经 还是 因为 所以 但是 如果 这些 那些 我们 你们 他们 她们 它们
一样 一直 一定 不过 不能 不是 这么 那么 突然 似乎 仿佛 觉得 看着 看到
听到 想到 感觉 应该 只是 就是 还有 然后 这时 此时 这里 那里 一些 有些
东西 事情 时间 地方 样子 一点 一切 所有 之后 之前 当然 也许 或许 终于
竟然 居然 顿时 立刻 立即 马上 刚才 刚刚 周围 旁边 身边 心里 心中 眼中
眼前 面前 身上 身后 头上 手中 不会 不要 不想 不敢 没想 想要 一声 看见
听见 不过 这是 那是 一下 两人 这才 之类 之间 当中 当时 后来 后面 前面
一旁 一行 众人 一群 几乎 完全 根本 简直 等等 大概 估计 反而 重新 继续
开始 结束 一般 普通 时间 地方 问题 办法 方法 情况 样子 模样 声音 时刻
两个 三个 几个 那种 这种 一种 自然 似的 一边 那边 这边 别人 大家 整个
有人 没人 什么样 不停 不由 不禁 不住 一时 半天 一阵 片刻 随即 接着 于是
而且 然而 不仅 甚至 反正 毕竟 何况 难道 无非 只有 只要 即使 哪怕 除非
什么的 怎么样 为什么 怎么办 一会儿 这会儿 那会儿 不知道 没办法 没关系
开始 结束 虽然 但是 然而 不过 而且 因此 于是 接着 随后 出现 发现 表示
认为 觉得 决定 准备 继续 重新 保持 进行 成为 拥有 获得 需要 可能 必须
应该 能够 无法 不能 不会 似乎 仿佛 居然 竟然 果然 当然 显然 难道 究竟
那些 这些 任何 所有 整个 全部 部分 一些 许多 很多 不少 大量 无数 几个
""".split())

# Generic narration verbs/adverbs that jieba tags as nouns/verbs but carry
# no platform-distinguishing signal.
_GENERIC_CONTENT: frozenset[str] = frozenset("""
说道 问道 喊道 笑道 答道 开口 摇头 点头 抬头 低头 转身 伸手 走来 走去
站着 坐着 躺着 看了 听了 想了 笑了 叹了 时候 样子 声音 目光 眼睛 脸上
笑容 神色 表情 动作 模样 心情 念头 脑海 脑中 不远处 远处 这一刻 一瞬间
""".split())


def _is_common(w: str) -> bool:
    return w in _COMMON_WORDS or w in _GENERIC_CONTENT


# jieba POS tags worth keeping as content keywords: nouns, proper nouns
# (person/place/org/other), idioms. Everything else (pronouns, adverbs,
# particles, generic verbs) is dropped.
_KEEP_POS_PREFIX = ("n", "nr", "ns", "nt", "nz", "nl", "ng", "i")

# Particles/demonstratives/interrogatives that never occur inside a Chinese
# proper noun — their presence means the candidate is a prose fragment
# (前的青云宗 / 道这意味 / 手里的玄) rather than a real word/coined term.
_HARD_REJECT_CHARS = set("的了着过吗呢吧么哦啊呀嘛又再就才将这那什怎谁和与或及我你他她它")
# Pronouns / common verb-adverb chars: tolerated singly but a high ratio
# signals ordinary prose, not a content phrase. NOTE: 能 / 力 are content
# chars in domain coinages (灵能 / 异能 / 能力 / 威力) and must NOT be here.
_SOFT_FUNC_CHARS = set("会在是有就都也很把被让从向往要去"
                       "们没看说想还只如果因所以但必须")


def _is_content_phrase(term: str, max_len: int = 6) -> bool:
    """A multi-char run that reads like a real word / proper noun rather
    than a prose fragment or everyday function word."""
    if not (2 <= len(term) <= max_len):
        return False
    if not re.fullmatch(r"[一-鿿]+", term):
        return False
    if _is_common(term):
        return False
    if any(ch in _HARD_REJECT_CHARS for ch in term):
        return False
    if any(cw in term for cw in _COMMON_WORDS):    # 自己怎么会 / 要进去吗
        return False
    # Func-char ratio is a glue heuristic for longer runs; a 2-3 字 domain
    # word (灵能 / 异能) with one borderline char must not be rejected.
    if len(term) >= 4 and sum(1 for ch in term if ch in _SOFT_FUNC_CHARS) / len(term) > 0.34:
        return False
    if all(ch in _STOPWORDS for ch in term):
        return False
    return True


def _multichar_pool(joined: str, pmi: bool = True) -> set[str]:
    """Recall multi-char phrase candidates (2-6 字) via 后缀模式 + jieba
    unknown-word discovery, optionally char-level PMI 凝合度. PMI is the
    only recall when jieba is unavailable, but it glues adjacent words
    (凡者都拥) — so callers that have jieba word boundaries pass
    ``pmi=False`` for cleaner candidates (青云宗, 玄天剑, 超凡能力)."""
    pool: set[str] = set()
    try:
        from .neologism_extractor import (
            _char_pattern_match, _ngram_pmi, _recall_via_jieba,
        )
        if pmi:
            pool |= _ngram_pmi(joined)
        pool |= _char_pattern_match(joined)
        pool |= {w for w in _recall_via_jieba(joined) if len(w) >= 2}
    except Exception:
        pass
    return pool


def _dedup_substrings(cands: Counter) -> list[str]:
    """Drop a fragment when a longer candidate contains it and occurs about
    as often (李慕白 → drop 慕白/李慕; 宗门大比 → drop 宗门大/大比). Keeps
    the longest coherent term, preferring length then frequency."""
    ordered = sorted(cands, key=lambda t: (-len(t), -cands[t]))
    kept: list[str] = []
    for term in ordered:
        covered = any(
            term != longer and term in longer and cands[longer] >= cands[term] * 0.6
            for longer in kept
        )
        if not covered:
            kept.append(term)
    return kept


_JIEBA_FREQ: dict | None = None


def _general_freq() -> dict:
    """jieba's loaded dictionary frequencies — a proxy for how common a
    word is in *general* Chinese. A word that is frequent in our corpus
    but rare/absent in general Chinese is a domain coinage (keyness)."""
    global _JIEBA_FREQ
    if _JIEBA_FREQ is not None:
        return _JIEBA_FREQ
    try:
        import jieba
        jieba.initialize()
        _JIEBA_FREQ = dict(jieba.dt.FREQ)
    except Exception:
        _JIEBA_FREQ = {}
    return _JIEBA_FREQ


def _rarity(term: str, freq: dict, divisor: float = 100.0) -> float:
    """Weight in (0,1]: ~1.0 for words absent/rare in general Chinese
    (coinages), small for everyday high-frequency words."""
    import math
    gf = freq.get(term, 0)
    return 1.0 / (1.0 + math.log10(1.0 + gf / divisor))


def _load_known_words() -> set[str]:
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
    return known


def _top_words(texts: list[str], k: int = 20) -> list[dict[str, Any]]:
    """高频词 — representative content keywords, mixed length. jieba POS
    keeps nouns / proper nouns; PMI / 后缀 recall adds longer terms jieba
    breaks apart. Ranked by frequency × a keyness weight (down-weighting
    words common in general Chinese) so 域内词 surface above generic ones."""
    joined = "\n".join(texts)
    freq = _general_freq()
    counter: Counter = Counter()

    try:
        import jieba.posseg as _pseg
        for t in texts:
            for tok in _pseg.cut(t):
                w, flag = tok.word, tok.flag
                if (len(w) >= 2 and re.fullmatch(r"[一-鿿]+", w)
                        and flag.startswith(_KEEP_POS_PREFIX)
                        and not _is_common(w)):
                    counter[w] += 1
    except Exception:
        try:
            import jieba
            for t in texts:
                for w in jieba.cut(t):
                    if (len(w) >= 2 and re.fullmatch(r"[一-鿿]+", w)
                            and not _is_common(w) and w not in _STOPWORDS):
                        counter[w] += 1
        except Exception:
            chars = re.sub(r"[^一-鿿]", "", joined)
            for i in range(len(chars) - 1):
                bg = chars[i:i + 2]
                if (bg[0] not in _STOPWORDS and bg[1] not in _STOPWORDS
                        and not _is_common(bg)):
                    counter[bg] += 1

    for term in _multichar_pool(joined, pmi=not freq):
        if len(term) >= 3 and _is_content_phrase(term):
            n = joined.count(term)
            if n >= 2:
                counter[term] = max(counter.get(term, 0), n)

    if not counter:
        return []
    kept = set(_dedup_substrings(counter))
    ranked = sorted(
        (t for t in counter if t in kept),
        key=lambda t: counter[t] * _rarity(t, freq, 1000.0) * (1.0 + 0.12 * (len(t) - 2)),
        reverse=True,
    )
    return [{"word": t, "count": counter[t]} for t in ranked[:k]]


# Open-class POS tags that can carry a coined noun (including HMM-guessed
# new words). Verbs (开始), conjunctions (虽然), adverbs, particles are NOT
# here — that single filter removes most everyday-word false positives.
_COINABLE_POS = ("n", "nz", "nr", "ns", "nt", "nrt", "nrfg", "l", "j")


def _neologism_step1(texts: list[str], k: int = 15) -> list[dict[str, Any]]:
    """生造词 Step1 初筛 — 目标是「域内新造名词」(超凡能力 / 灵能 / 异能 /
    天赋 / 序列 …) 而非常用词 (开始 / 虽然)。综合三重信号，而非只靠频率/PMI:

    1. 词性: 仅取名词类 (jieba POS)，直接排除动词/连词/副词等常用词。
    2. 未登录 (OOV): jieba 词典里没有、但语料里反复出现的多字词，多为
       作者自造的复合专名 (超凡能力)。
    3. keyness: 用 jieba 通用词频做基线，语料频次高而通用频次低者得分
       高 — 把「序列」「天赋」这类常用字面、域内高频的词顶上来。
    """
    known = _load_known_words()
    joined = "\n".join(texts)
    freq = _general_freq()
    cands: Counter = Counter()
    has_jieba = False

    try:
        import jieba.posseg as _pseg
        has_jieba = True
        bigrams: Counter = Counter()
        for t in texts:
            prev: tuple[str, str] | None = None
            for tok in _pseg.cut(t):
                w, flag = tok.word, tok.flag
                cjk = bool(re.fullmatch(r"[一-鿿]+", w))
                if (cjk and 2 <= len(w) <= 6 and flag.startswith(_COINABLE_POS)
                        and _is_content_phrase(w) and w not in known):
                    cands[w] += 1
                # Adjacent (modifier + noun-head) → OOV compound coinage,
                # respecting word boundaries (超凡 + 能力 = 超凡能力). Far
                # cleaner than char-level PMI which glues across words.
                if prev is not None and cjk:
                    pw, pf = prev
                    merged = pw + w
                    if (flag.startswith("n") and 3 <= len(merged) <= 6
                            and pf[:1] in ("n", "v", "a", "b", "z")
                            and freq.get(pw, 0) < 500          # uncommon modifier = coinage signal
                            and freq.get(merged, 0) == 0
                            and _is_content_phrase(merged) and merged not in known):
                        bigrams[merged] += 1
                prev = (w, flag) if cjk else None
        for m, c in bigrams.items():
            if c >= 2:
                cands[m] = max(cands.get(m, 0), c)
    except Exception:
        has_jieba = False

    # OOV single words / suffix-pattern coinages jieba keeps as tokens; only
    # those ABSENT from the general dictionary are coinages. Skip the noisy
    # char-PMI recall when jieba word boundaries are available.
    for term in _multichar_pool(joined, pmi=not has_jieba):
        if not _is_content_phrase(term) or term in known:
            continue
        if has_jieba and freq.get(term, 0) > 0:
            continue
        n = joined.count(term)
        if n >= 2 and term not in cands:
            cands[term] = n

    if not cands:
        return []
    kept = _dedup_substrings(cands)
    result = sorted(
        kept,
        key=lambda t: cands[t] * _rarity(t, freq, 100.0) * (1.0 + 0.1 * (len(t) - 2)),
        reverse=True,
    )[:k]
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
