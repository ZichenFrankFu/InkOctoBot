"""Phase 2 NLP features — pure-Python, zero-LLM.

Each extractor returns a flat dict matching the ``chapter_features``
column names. Tries to use ``jieba`` when available; degrades to
character-level heuristics when it isn't, so the pipeline still
runs in environments without the lib installed.
"""
from __future__ import annotations

import logging
import math
import re
from collections import Counter
from typing import Any

from .dictionaries import (
    load_classical_words, load_idioms, load_internet_slang,
)

logger = logging.getLogger("inkoctobot.market_extractor.nlp_features")


# Whether jieba is importable. Probed once at module load.
try:
    import jieba  # noqa: F401
    import jieba.posseg as _posseg  # noqa: F401
    _HAS_JIEBA = True
except Exception:
    _HAS_JIEBA = False


_CJK = re.compile(r"[一-鿿]")
_CJK_OR_ALNUM = re.compile(r"[一-鿿\w]")
_SENTENCE_BREAKS = re.compile(r"[。！？!?…]+")
_SHORT_SENTENCE_THRESHOLD = 8     # chars
_LONG_SENTENCE_THRESHOLD = 30


def _safe_div(a: float, b: float) -> float:
    return a / b if b else 0.0


def _percentile(values: list[int], p: float) -> int:
    if not values:
        return 0
    s = sorted(values)
    k = max(0, min(len(s) - 1, int(round((p / 100) * (len(s) - 1)))))
    return s[k]


def _tokenize(text: str) -> list[str]:
    """Token list. Uses jieba if present; otherwise per-char."""
    if _HAS_JIEBA:
        return [t for t in jieba.cut(text) if t.strip()]
    # Char-level fallback — every CJK char is its own token, punctuation
    # joined with adjacent ASCII letters as a single token.
    return [c for c in text if _CJK_OR_ALNUM.match(c)]


def _pos_tagged(text: str) -> list[tuple[str, str]]:
    """Return (word, pos) pairs. Empty list on jieba absence — densities
    fall back to 0.0, which downstream Phase 4 stats explicitly tolerate."""
    if not _HAS_JIEBA:
        return []
    return [(w.word, w.flag) for w in _posseg.cut(text) if w.word.strip()]


def _split_sentences(text: str) -> list[str]:
    parts = _SENTENCE_BREAKS.split(text)
    return [p.strip() for p in parts if p.strip()]


def _split_paragraphs(text: str) -> list[str]:
    return [p for p in (l.strip() for l in text.split("\n")) if p]


def _is_dialogue_paragraph(p: str) -> bool:
    return p.startswith(("“", "\"", "「", "『"))


# ─────────── individual feature blocks ───────────


def _basic_counts(text: str, paragraphs: list[str], sentences: list[str]) -> dict[str, Any]:
    cjk_chars = sum(1 for c in text if _CJK.match(c))
    return {
        "word_count":          cjk_chars,
        "paragraph_count":     len(paragraphs),
        "chapter_title_length": 0,    # set by caller if title is known
    }


def _punctuation_features(text: str) -> dict[str, Any]:
    n = max(1, len(text))
    exc = text.count("！") + text.count("!")
    que = text.count("？") + text.count("?")
    ell = text.count("…") + text.count("...")
    quo = text.count("“") + text.count("”") + text.count("\"")
    half = sum(1 for c in text if c.isascii())
    return {
        "exclamation_density": _safe_div(exc, n),
        "question_density":    _safe_div(que, n),
        "ellipsis_density":    _safe_div(ell, n),
        "dash_count":          text.count("—") + text.count("——"),
        "quotation_density":   _safe_div(quo, n),
        "halfwidth_ratio":     _safe_div(half, n),
    }


def _sentence_length_features(sentences: list[str]) -> dict[str, Any]:
    if not sentences:
        return {
            "avg_sentence_length": 0.0,
            "short_sentence_ratio": 0.0,
            "long_sentence_ratio":  0.0,
        }
    lens = [len(s) for s in sentences]
    return {
        "avg_sentence_length": sum(lens) / len(lens),
        "short_sentence_ratio": _safe_div(
            sum(1 for l in lens if l <= _SHORT_SENTENCE_THRESHOLD),
            len(lens),
        ),
        "long_sentence_ratio": _safe_div(
            sum(1 for l in lens if l >= _LONG_SENTENCE_THRESHOLD),
            len(lens),
        ),
    }


def _paragraph_features(paragraphs: list[str]) -> dict[str, Any]:
    if not paragraphs:
        return {
            "paragraph_length_p25": 0, "paragraph_length_p50": 0,
            "paragraph_length_p75": 0, "paragraph_length_p95": 0,
            "single_sentence_paragraph_ratio": 0.0,
            "dialogue_paragraph_ratio": 0.0,
            "description_paragraph_ratio": 0.0,
        }
    lens = [len(p) for p in paragraphs]
    dial = sum(1 for p in paragraphs if _is_dialogue_paragraph(p))
    single = sum(
        1 for p in paragraphs
        if len(_SENTENCE_BREAKS.findall(p)) <= 1
    )
    return {
        "paragraph_length_p25": _percentile(lens, 25),
        "paragraph_length_p50": _percentile(lens, 50),
        "paragraph_length_p75": _percentile(lens, 75),
        "paragraph_length_p95": _percentile(lens, 95),
        "single_sentence_paragraph_ratio": _safe_div(single, len(paragraphs)),
        "dialogue_paragraph_ratio": _safe_div(dial, len(paragraphs)),
        "description_paragraph_ratio": _safe_div(
            len(paragraphs) - dial - single, len(paragraphs),
        ),
    }


def _pos_density_features(text: str) -> dict[str, Any]:
    pos = _pos_tagged(text)
    if not pos:
        return {
            "adjective_density": 0.0, "verb_density": 0.0,
            "adverb_density":    0.0,
            "noun_density":      0.0, "verb_density_v2": 0.0,
            "adjective_density_v2": 0.0, "pace_ratio_json": "",
        }
    total = len(pos)
    n_adj = sum(1 for _, p in pos if p.startswith("a"))
    n_v   = sum(1 for _, p in pos if p.startswith("v"))
    n_d   = sum(1 for _, p in pos if p.startswith("d"))
    n_n   = sum(1 for _, p in pos if p.startswith("n"))
    import json
    return {
        "adjective_density":    _safe_div(n_adj, total),
        "verb_density":         _safe_div(n_v, total),
        "adverb_density":       _safe_div(n_d, total),
        "noun_density":         _safe_div(n_n, total),
        "verb_density_v2":      _safe_div(n_v, total),
        "adjective_density_v2": _safe_div(n_adj, total),
        "pace_ratio_json":      json.dumps({
            "noun": _safe_div(n_n, total),
            "verb": _safe_div(n_v, total),
            "adj":  _safe_div(n_adj, total),
        }, ensure_ascii=False),
    }


def _vocab_diversity_features(text: str) -> dict[str, Any]:
    tokens = _tokenize(text)
    if not tokens:
        return {
            "ttr": 0.0, "idiom_density": 0.0,
            "classical_word_density": 0.0, "internet_slang_density": 0.0,
            "top_50_words_json": "[]",
        }
    types = len(set(tokens))
    idioms = load_idioms()
    classical = load_classical_words()
    slang = load_internet_slang()
    n_idiom = sum(1 for t in tokens if t in idioms)
    n_class = sum(1 for t in tokens if t in classical)
    n_slang = sum(1 for t in tokens if t in slang)
    import json
    top50 = Counter(tokens).most_common(50)
    return {
        "ttr":                    _safe_div(types, len(tokens)),
        "idiom_density":          _safe_div(n_idiom, len(tokens)),
        "classical_word_density": _safe_div(n_class, len(tokens)),
        "internet_slang_density": _safe_div(n_slang, len(tokens)),
        "top_50_words_json":      json.dumps(top50, ensure_ascii=False),
    }


def _rhetoric_features(text: str, sentences: list[str]) -> dict[str, Any]:
    # Rough heuristics — counts of marker patterns.
    parallel = len(re.findall(r"(.{2,5})[，,].*?\1", text))
    person = sum(1 for w in ("它说", "他说", "她说") if w in text)
    rhet_q = sum(1 for s in sentences if s.endswith(("吗", "呢", "？")))
    repetition = 0
    for s in sentences:
        if len(s) >= 4:
            for k in range(2, min(5, len(s) // 2 + 1)):
                sub = s[:k]
                if s.count(sub) >= 3:
                    repetition += 1
                    break
    # Metaphor density: count "X如Y" / "X像Y" markers.
    metaphor = len(re.findall(r"[一-鿿]+[如像若]+[一-鿿]+", text))
    # Onomatopoeia heuristic: any char repeated 2+ times consecutively.
    onom = len(re.findall(r"([一-鿿])\1{1,}", text))
    n = max(1, sum(1 for c in text if _CJK.match(c)))
    return {
        "parallelism_count":         parallel,
        "personification_count":     person,
        "rhetorical_question_count": rhet_q,
        "repetition_pattern_count":  repetition,
        "metaphor_density":          _safe_div(metaphor, n),
        "onomatopoeia_count":        onom,
    }


def _dialogue_features(paragraphs: list[str], text: str) -> dict[str, Any]:
    dial_paras = [p for p in paragraphs if _is_dialogue_paragraph(p)]
    n_chars = max(1, len(text))
    dialogue_chars = sum(len(p) for p in dial_paras)
    # Dialogue-tag diversity: count unique "X说/X道/X笑道" patterns.
    tags = re.findall(r"[一-鿿]+(?:说|道|笑|喊|叫|喃|嘀)道?", text)
    tag_diversity = _safe_div(len(set(tags)), max(1, len(tags)))
    # Internal monologue: paragraphs starting with 心 or wrapping in 「.
    mono = sum(1 for p in paragraphs if p.startswith(("心想", "心道", "「")))
    # Consecutive dialogue turns: longest run of dialogue paragraphs.
    longest = 0
    cur = 0
    for p in paragraphs:
        if _is_dialogue_paragraph(p):
            cur += 1
            longest = max(longest, cur)
        else:
            cur = 0
    return {
        "dialogue_ratio":         _safe_div(dialogue_chars, n_chars),
        "dialogue_tag_diversity": tag_diversity,
        "monologue_ratio":        _safe_div(mono, max(1, len(paragraphs))),
        "avg_dialogue_turns":     float(longest),
    }


def _chapter_micro_features(
    text: str, paragraphs: list[str], sentences: list[str],
) -> dict[str, Any]:
    first_s = sentences[0] if sentences else ""
    last_s = sentences[-1] if sentences else ""
    first_p = paragraphs[0] if paragraphs else ""
    # First-sentence type heuristic.
    if first_s.startswith(("“", "「")):
        fs_type = "dialogue"
    elif any(w in first_s for w in ("看", "望", "见", "瞧")):
        fs_type = "description"
    else:
        fs_type = "action"
    # Scene change: count blank-line-separated blocks.
    blocks = [b for b in re.split(r"\n\s*\n", text) if b.strip()]
    return {
        "first_sentence_type":    fs_type,
        "first_sentence_length":  len(first_s),
        "first_paragraph_length": len(first_p),
        "last_sentence_length":   len(last_s),
        "last_200_chars_emotion": "",  # LLM-tagged later
        "scene_change_count":     max(0, len(blocks) - 1),
    }


def _tfidf_top_words(
    text: str, all_chapter_texts: list[str] | None = None, top_n: int = 30,
) -> dict[str, Any]:
    """Per-chapter top words by TF-IDF when corpus is provided; falls
    back to plain TF when called in isolation."""
    import json
    tokens = [t for t in _tokenize(text) if len(t) >= 2]
    if not tokens:
        return {"tfidf_top_words_json": "[]"}
    tf = Counter(tokens)
    if not all_chapter_texts:
        return {"tfidf_top_words_json": json.dumps(tf.most_common(top_n), ensure_ascii=False)}
    n_docs = len(all_chapter_texts)
    df: Counter[str] = Counter()
    for doc in all_chapter_texts:
        df.update(set(t for t in _tokenize(doc) if len(t) >= 2))
    scored: list[tuple[str, float]] = []
    for term, freq in tf.items():
        idf = math.log((n_docs + 1) / (df.get(term, 0) + 1)) + 1
        scored.append((term, freq * idf))
    scored.sort(key=lambda x: x[1], reverse=True)
    return {"tfidf_top_words_json": json.dumps(
        [[t, round(s, 3)] for t, s in scored[:top_n]], ensure_ascii=False,
    )}


# ─────────── public API ───────────


def extract(
    chapter_text: str, *,
    chapter_title: str = "",
    corpus_texts: list[str] | None = None,
) -> dict[str, Any]:
    """Compute every NLP feature for one chapter. Output dict's keys
    match ``chapter_features`` column names (excluding LLM-extracted
    fields). ``corpus_texts`` enables proper TF-IDF; without it falls
    back to TF."""
    text = chapter_text or ""
    paragraphs = _split_paragraphs(text)
    sentences = _split_sentences(text)
    out: dict[str, Any] = {}
    out.update(_basic_counts(text, paragraphs, sentences))
    out["chapter_title_length"] = len(chapter_title or "")
    out.update(_punctuation_features(text))
    out.update(_sentence_length_features(sentences))
    out.update(_paragraph_features(paragraphs))
    out.update(_pos_density_features(text))
    out.update(_vocab_diversity_features(text))
    out.update(_rhetoric_features(text, sentences))
    out.update(_dialogue_features(paragraphs, text))
    out.update(_chapter_micro_features(text, paragraphs, sentences))
    out.update(_tfidf_top_words(text, corpus_texts))
    return out
