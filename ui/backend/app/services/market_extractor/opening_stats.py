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


_SURNAMES = set(
    "王李张刘陈杨黄赵吴周徐孙马朱胡郭何高林罗郑梁谢宋唐许韩冯邓曹彭曾"
    "肖田董袁潘于蒋蔡余杜叶程苏魏吕丁任沈姚卢姜崔钟谭陆汪范金石廖贾夏"
    "韦付方白邹孟熊秦邱江尹薛闫段雷侯龙史陶黎贺顾毛郝龚邵万钱严覃武戴"
    "莫孔向汤"
)


def _looks_like_person_name(term: str, flag: str) -> bool:
    """Heuristic: a jieba 'nr*'-tagged 2-4 字 term whose first char is a
    common surname is a character name → excluded from 高频词 / 生造词. The
    surname check spares coinages jieba mis-tags as names (灵能 / 异能 — 灵 /
    异 are not surnames)."""
    return flag.startswith("nr") and 2 <= len(term) <= 4 and term[0] in _SURNAMES


def _keep_noun(flag: str) -> bool:
    """Noun-class POS, EXCLUDING pure person-name tags handled separately —
    we keep 'nr' here (jieba over-tags coinages as nr) and filter actual
    names via the surname heuristic instead."""
    return flag.startswith("n") or flag in ("l", "j")


def _extract_tokens(texts: list[str], freq: dict):
    """ONE jieba.posseg pass over the (already corpus-capped) texts →
    noun-token frequencies (keyed by word, carrying a name flag) and clean
    (modifier+noun) bigram frequencies for split-compound recovery. Doing
    this once — and never calling ``str.count`` per candidate — is what
    keeps the opening-NLP compute from blowing up to O(candidates×corpus)
    (the cause of the「永远分析中」hang)."""
    token_counts: Counter = Counter()      # word -> count
    token_isname: dict[str, bool] = {}
    bigram_counts: Counter = Counter()
    try:
        import jieba.posseg as _pseg
    except Exception:
        return token_counts, token_isname, bigram_counts, False
    for t in texts:
        prev: tuple[str, str] | None = None
        for tok in _pseg.cut(t):
            w, flag = tok.word, tok.flag
            cjk = len(w) >= 2 and bool(re.fullmatch(r"[一-鿿]+", w))
            if cjk and len(w) <= 6:
                token_counts[w] += 1
                if w not in token_isname:
                    token_isname[w] = _looks_like_person_name(w, flag)
                if prev is not None:
                    pw, pf = prev
                    merged = pw + w
                    if (flag.startswith("n") and 3 <= len(merged) <= 6
                            and pf[:1] in ("n", "v", "a", "b", "z")
                            and freq.get(pw, 0) < 500
                            and freq.get(merged, 0) == 0):
                        bigram_counts[merged] += 1
                prev = (w, flag)
            else:
                prev = None
    return token_counts, token_isname, bigram_counts, True


def _top_words(token_counts: Counter, token_isname: dict, bigram_counts: Counter,
               freq: dict, has_jieba: bool, texts: list[str],
               k: int = 20) -> list[dict[str, Any]]:
    """高频词 — representative content keywords, mixed length, no person
    names. Ranked by frequency × keyness (down-weighting words common in
    general Chinese) so 域内词 rise above generic ones."""
    counter: Counter = Counter()
    if has_jieba:
        for w, c in token_counts.items():
            if (not _is_common(w) and not token_isname.get(w)
                    and _is_content_phrase(w)):
                counter[w] += c
        for m, c in bigram_counts.items():
            if c >= 2 and not _is_common(m) and _is_content_phrase(m):
                counter[m] = max(counter.get(m, 0), c)
    else:
        # No jieba: light char-bigram fallback on the capped corpus.
        joined = "\n".join(texts)
        chars = re.sub(r"[^一-鿿]", "", joined)
        for i in range(len(chars) - 1):
            bg = chars[i:i + 2]
            if (bg[0] not in _STOPWORDS and bg[1] not in _STOPWORDS
                    and not _is_common(bg)):
                counter[bg] += 1

    if not counter:
        return []
    kept = set(_dedup_substrings(counter))
    ranked = sorted(
        (t for t in counter if t in kept),
        key=lambda t: counter[t] * _rarity(t, freq, 1000.0) * (1.0 + 0.12 * (len(t) - 2)),
        reverse=True,
    )
    return [{"word": t, "count": counter[t]} for t in ranked[:k]]


def _neologism_step1(token_counts: Counter, token_isname: dict, bigram_counts: Counter,
                     freq: dict, known: set, has_jieba: bool, texts: list[str],
                     k: int = 15) -> list[dict[str, Any]]:
    """生造词 Step1 初筛 — 域内新造名词 (超凡能力 / 灵能 / 异能 / 天赋 /
    序列…)，排除人名与常用词。三重信号: 名词词性 + 未登录(OOV) + keyness。"""
    # A coinage is a noun rare in GENERAL Chinese but frequent here — this
    # threshold keeps OOV words (灵能=0) AND real words used as genre terms
    # (序列=803 / 天赋=813 / 异能=26) while excluding everyday nouns
    # (世界≈34k / 能力≈19k).
    _RARE_GENERAL = 3000
    cands: Counter = Counter()
    if has_jieba:
        for w, c in token_counts.items():
            if (freq.get(w, 0) < _RARE_GENERAL and not token_isname.get(w)
                    and not _is_common(w) and w not in known
                    and _is_content_phrase(w)):
                cands[w] += c
        # Split-compound coinages (超凡 + 能力) — OOV merged form.
        for m, c in bigram_counts.items():
            if (c >= 2 and m not in known and not _is_common(m)
                    and _is_content_phrase(m)):
                cands[m] = max(cands.get(m, 0), c)
    else:
        # No jieba: suffix-pattern + PMI recall on the capped corpus.
        joined = "\n".join(texts)
        for term in _multichar_pool(joined, pmi=True):
            if not _is_content_phrase(term) or term in known:
                continue
            if term[0] in _SURNAMES and len(term) <= 4:   # drop names
                continue
            n = joined.count(term)
            if n >= 2:
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

    # Keyword/neologism extraction runs jieba — cap the corpus so the
    # compute stays fast (representative on a sample; the spec 字数/句长/
    # 标点 counts above already use the full corpus). ONE jieba pass feeds
    # both 高频词 and 生造词.
    _KW_CHAR_CAP = 120_000
    kw_texts: list[str] = []
    acc = 0
    for t in texts:
        kw_texts.append(t)
        acc += len(t)
        if acc >= _KW_CHAR_CAP:
            break
    freq = _general_freq()
    known = _load_known_words()
    tok_counts, tok_isname, bigrams, has_jieba = _extract_tokens(kw_texts, freq)

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
        # 高频词 + 生造词 Step1（共用一次 jieba 分词结果）
        "top_words": _top_words(tok_counts, tok_isname, bigrams, freq, has_jieba, kw_texts),
        "neologism_step1": _neologism_step1(tok_counts, tok_isname, bigrams, freq, known, has_jieba, kw_texts),
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
