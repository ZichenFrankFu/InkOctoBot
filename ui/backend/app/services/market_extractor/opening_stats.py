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

# 常用词 / 姓氏 / 音译字 现由可编辑、可热更新的配置文件加载（resources/
# wordlists/*.txt + 用户 overlay，见 wordlists.py），数据来源可靠（HIT/Baidu
# 停用词表 + 百家姓/日文姓 + 新华社译音表），而非硬编码。用户在「资源管理」
# 里把某词归为人名/常用词后，reload_wordlists() 让本进程立即生效（再配合清理
# opening_nlp 缓存触发重算）。
try:
    from . import wordlists as _wl
except Exception:  # pragma: no cover
    _wl = None

_COMMON_WORDS: frozenset[str] = frozenset()
# Multi-char subset only: substring-rejecting a candidate on a *single-char*
# common word (停用词表含「能 / 力 / 会 / 在」等单字)会误杀 灵能 / 异能 / 能力
# 这类域内复合词 — 单字虚词另由 _HARD_REJECT_CHARS 把关。
_COMMON_MULTI: frozenset[str] = frozenset()
_SURNAMES: frozenset[str] = frozenset()
_TRANSLIT_CHARS: frozenset[str] = frozenset()


def reload_wordlists() -> None:
    """Refresh the in-process 常用词/姓氏/音译字 sets from wordlists.py — called
    after a 资源管理 add/remove so the next analysis re-filters with the edit."""
    global _COMMON_WORDS, _COMMON_MULTI, _SURNAMES, _TRANSLIT_CHARS
    if _wl is None:
        return
    try:
        _COMMON_WORDS = _wl.load_common_words()
        _COMMON_MULTI = frozenset(w for w in _COMMON_WORDS if len(w) >= 2)
        _SURNAMES = _wl.load_surnames()
        _TRANSLIT_CHARS = _wl.load_translit_chars()
    except Exception:  # pragma: no cover - resources always present
        pass


reload_wordlists()

# Strong name-ending characters (音译名 / 角色名常见尾字).
_NAME_END_CHARS = set("丝娜莉娅妮克特斯尔薇露琳蒂娃曼茜黛缇媛婭菈珂")


def _is_common(w: str) -> bool:
    return w in _COMMON_WORDS


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
    if any(cw in term for cw in _COMMON_MULTI):    # 自己怎么会 / 要进去吗
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


def _looks_like_foreign_name(term: str) -> bool:
    """Transliterated foreign name (英/日 音译) — e.g. 爱丽丝 / 艾莉丝 / 玛丽亚
    / 杰克逊. A ≥3 字 word that is dominated by 音译字 or ends in a strong
    name char, and isn't a common word."""
    if len(term) < 3 or len(term) > 6 or _is_common(term):
        return False
    if not re.fullmatch(r"[一-鿿]+", term):
        return False
    tr = sum(1 for ch in term if ch in _TRANSLIT_CHARS)
    if tr >= max(2, (len(term) + 1) // 2):
        return True
    # ends in a strong name char + at least one other 音译字 (爱丽丝: 丝 + 丽?)
    if term[-1] in _NAME_END_CHARS and tr >= 1:
        return True
    return False


def _looks_like_person_name(term: str, flag: str, freq: dict | None = None) -> bool:
    """Whether a token is a character name → excluded from 高频词 / 生造词.

    Three triggers:
      1. Chinese name: first char is a 百家姓 surname (config: surnames.txt;
         spares coinages jieba mis-tags as names — 灵能 / 异能, 灵/异 aren't
         surnames) AND either jieba tagged it 'nr*' OR it's a surname-led 2-3
         字 word jieba doesn't know (freq==0; real words like 王朝/李子 are in
         the dict freq>0).
      2. Japanese surname prefix (上杉 / 武田 …) in surnames.txt.
      3. Transliterated foreign name (音译字, see _looks_like_foreign_name).
    """
    # The whole token IS a surname (e.g. a standalone 上杉 / 欧阳 token).
    if term in _SURNAMES:
        return True
    if 2 <= len(term) <= 4 and term[0] in _SURNAMES:
        if flag.startswith("nr"):
            return True
        if freq is not None and len(term) <= 3 and freq.get(term, 0) == 0:
            return True
    # Compound-surname prefix (上杉绘梨衣 → 上杉…).
    for ln in (3, 2):
        if len(term) >= ln + 1 and term[:ln] in _SURNAMES:
            return True
    if _looks_like_foreign_name(term):
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
    token_pos: dict[str, str] = {}         # word -> jieba POS flag (first seen)
    bigram_counts: Counter = Counter()
    try:
        import jieba.posseg as _pseg
    except Exception:
        return token_counts, token_isname, token_pos, bigram_counts, False
    for t in texts:
        prev: tuple[str, str] | None = None
        for tok in _pseg.cut(t):
            w, flag = tok.word, tok.flag
            cjk = len(w) >= 2 and bool(re.fullmatch(r"[一-鿿]+", w))
            if cjk and len(w) <= 6:
                token_counts[w] += 1
                if w not in token_isname:
                    token_isname[w] = _looks_like_person_name(w, flag, freq)
                    token_pos[w] = flag
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
    return token_counts, token_isname, token_pos, bigram_counts, True


def _top_words(token_counts: Counter, token_isname: dict, token_pos: dict,
               bigram_counts: Counter, freq: dict, has_jieba: bool,
               texts: list[str], k: int = 20) -> list[dict[str, Any]]:
    """高频词 — representative content keywords, mixed length, no person
    names. Ranked by frequency × keyness (down-weighting words common in
    general Chinese) so 域内词 rise above generic ones. Restricted to
    noun-class POS so everyday 副词/动词 (马上 'd' / 感觉 'v') never surface
    as 高频词 — 高频词 应是内容名词，不是叙述虚词。"""
    counter: Counter = Counter()
    if has_jieba:
        for w, c in token_counts.items():
            if (not _is_common(w) and not token_isname.get(w)
                    and _keep_noun(token_pos.get(w, ""))
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

def _example_sentence(blob: str, word: str, width: int = 80) -> str:
    """First sentence in ``blob`` containing ``word`` (含有高频词的一句话),
    trimmed/centered on the word for display."""
    for s in _SENT_SPLIT_RE.split(blob):
        if word in s:
            s = s.strip()
            if len(s) <= width:
                return s
            idx = s.find(word)
            start = max(0, idx - (width - len(word)) // 2)
            end = min(len(s), start + width)
            return ("…" if start > 0 else "") + s[start:end] + ("…" if end < len(s) else "")
    return ""


def _attach_examples(top_words: list[dict[str, Any]],
                     owner_joined: dict[str, str], k: int = 3) -> None:
    """For each 高频词, attach the top-``k`` works using it most + an example
    sentence from each (点击高频词查看). Mutates ``top_words`` in place."""
    if not owner_joined:
        return
    for item in top_words:
        word = item.get("word") or ""
        if not word:
            continue
        hits = [(title, blob.count(word), blob)
                for title, blob in owner_joined.items() if word in blob]
        hits.sort(key=lambda x: -x[1])
        item["examples"] = [
            {"title": title, "count": cnt, "sentence": _example_sentence(blob, word)}
            for title, cnt, blob in hits[:k]
        ]


def compute_opening_stats(
    rows: list[dict[str, Any]],
) -> dict[str, Any]:
    """Spec-dimension stats over opening chapters.

    ``rows``: [{"chapter_num": int, "text": str, "title"?: str}] — every
    collected opening chapter of the analyzed work set. ``title`` (作品名)
    powers the「点击高频词→使用最多的 top3 作品+片段」drill-down.
    """
    texts: list[str] = []
    owners: list[str] = []          # parallel 作品名 per text (for 高频词 examples)
    for r in rows:
        t = str(r.get("text") or "")
        if not t:
            continue
        texts.append(t)
        owners.append(str(r.get("title") or "").strip())
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
    owner_joined: dict[str, str] = {}   # 作品名 → 该作品参与分词的开篇文本
    acc = 0
    for t, owner in zip(texts, owners):
        kw_texts.append(t)
        if owner:
            owner_joined[owner] = owner_joined.get(owner, "") + t
        acc += len(t)
        if acc >= _KW_CHAR_CAP:
            break
    freq = _general_freq()
    known = _load_known_words()
    tok_counts, tok_isname, tok_pos, bigrams, has_jieba = _extract_tokens(kw_texts, freq)
    top_words = _top_words(tok_counts, tok_isname, tok_pos, bigrams, freq, has_jieba, kw_texts)
    _attach_examples(top_words, owner_joined)

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
        "top_words": top_words,
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
