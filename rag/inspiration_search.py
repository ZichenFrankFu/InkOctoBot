"""
Lexical similarity ranking for the inspiration library.

The inspiration library is a small, personal set of free-text idea
snippets (scenes, plot devices, character designs, …). Rather than
standing up a vector index + embedding backend for it, we rank with
TF-IDF cosine similarity over a tokenization that needs no segmenter:

  - ASCII runs -> lowercased word tokens
  - CJK runs   -> overlapping character bigrams

Bigrams give Chinese text a meaningful "shared phrase" signal without a
word segmenter, and the whole module is pure-stdlib so it never fails on
a missing dependency.
"""
from __future__ import annotations

import math
import re
from collections import Counter

_ASCII_RE = re.compile(r"[a-z0-9]+")
_CJK_RE = re.compile(r"[一-鿿]+")


def tokenize(text: str) -> list[str]:
    """ASCII word tokens + CJK character bigrams. Lowercased."""
    text = (text or "").lower()
    tokens: list[str] = _ASCII_RE.findall(text)
    for run in _CJK_RE.findall(text):
        if len(run) == 1:
            tokens.append(run)
        else:
            tokens.extend(run[i:i + 2] for i in range(len(run) - 1))
    return tokens


def _tf_idf_vectors(doc_tokens: list[list[str]]) -> list[dict[str, float]]:
    """TF-IDF weight vectors for a corpus of token lists."""
    n = len(doc_tokens) or 1
    df: Counter[str] = Counter()
    for toks in doc_tokens:
        df.update(set(toks))
    idf = {t: math.log((n + 1) / (c + 1)) + 1.0 for t, c in df.items()}
    vectors: list[dict[str, float]] = []
    for toks in doc_tokens:
        if not toks:
            vectors.append({})
            continue
        total = len(toks)
        tf = Counter(toks)
        vectors.append({
            t: (c / total) * idf.get(t, 1.0) for t, c in tf.items()
        })
    return vectors


def _cosine(a: dict[str, float], b: dict[str, float]) -> float:
    if not a or not b:
        return 0.0
    small, large = (a, b) if len(a) <= len(b) else (b, a)
    dot = sum(w * large.get(t, 0.0) for t, w in small.items())
    if dot == 0.0:
        return 0.0
    na = math.sqrt(sum(w * w for w in a.values()))
    nb = math.sqrt(sum(w * w for w in b.values()))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def rank_inspirations(
    query: str,
    items: list[dict],
    *,
    top_k: int = 30,
    exclude_id: str | None = None,
) -> list[dict]:
    """Rank `items` (dicts carrying id / title / content) by TF-IDF
    cosine similarity to `query`. Returns matching items, each with an
    added float ``score`` in [0, 1], best first. Zero-score items drop.
    """
    pool = [it for it in items if it.get("id") != exclude_id]
    if not pool or not (query or "").strip():
        return []
    doc_tokens = [
        tokenize(f"{it.get('title') or ''} {it.get('content') or ''}")
        for it in pool
    ]
    q_tokens = tokenize(query)
    # Build vectors over docs + query together so they share one IDF.
    vectors = _tf_idf_vectors(doc_tokens + [q_tokens])
    q_vec = vectors[-1]
    scored: list[tuple[float, dict]] = []
    for it, dv in zip(pool, vectors[:-1]):
        s = _cosine(q_vec, dv)
        if s > 0:
            scored.append((s, it))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [{**it, "score": round(s, 4)} for s, it in scored[:top_k]]
