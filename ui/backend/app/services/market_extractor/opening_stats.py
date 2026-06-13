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

# 常用词 / 通用叙事词 / 姓氏 现由可编辑的配置文件加载（resources/wordlists/
# *.txt，见 wordlists.py），而非硬编码在此 — 用户可增删扩展。
try:
    from . import wordlists as _wl
    _COMMON_WORDS: frozenset[str] = _wl.load_stopwords()
    _GENERIC_CONTENT: frozenset[str] = _wl.load_generic_content()
    _SURNAMES: frozenset[str] = _wl.load_surnames()
except Exception:  # pragma: no cover - resources always present
    _COMMON_WORDS = frozenset()
    _GENERIC_CONTENT = frozenset()
    _SURNAMES = frozenset()


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


def _looks_like_person_name(term: str, flag: str, freq: dict | None = None) -> bool:
    """Whether a token is a character name → excluded from 高频词 / 生造词.

    A name's first char is a 百家姓 surname (config: surnames.txt). The
    surname gate spares coinages jieba mis-tags as names (灵能 / 异能 — 灵 /
    异 aren't surnames). Two triggers:
      1. jieba directly tagged it 'nr*' (person name).
      2. a surname-led 2-3 字 word jieba doesn't know (freq==0) — real words
         like 王朝 / 李子 are in the dictionary (freq>0), coinages starting
         with a surname char are vanishingly rare, so OOV ⇒ a name.
    """
    if not (2 <= len(term) <= 4) or term[0] not in _SURNAMES:
        return False
    if flag.startswith("nr"):
        return True
    if freq is not None and len(term) <= 3 and freq.get(term, 0) == 0:
        return True
    return False


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
                    token_isname[w] = _looks_like_person_name(w, flag, freq)
                if prev is not None:
                    pw, pf = prev
                    merged = pw + w
                    if (flag.startswith("n") and 3 <= len(merged) <= 6
                            and pf[:1] in ("n", "v", "a", "b", "z")
                            and freq.get(pw, 0) < 500
                            and freq.get(merged, 0) == 0
                            # don't glue a character name onto a noun
                            # (孟浩 + 天赋 → 孟浩天赋)
                            and not _looks_like_person_name(pw, pf, freq)
                            and not _looks_like_person_name(merged, "n", freq)):
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
