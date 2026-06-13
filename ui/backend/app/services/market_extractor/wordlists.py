"""Modular word-list config for opening-chapter NLP (高频词 / 生造词).

Externalizes the static vocabularies that were hardcoded in
``opening_stats.py`` into editable resource files under
``resources/wordlists/`` so they're proper config, not code:

- ``common_words.txt``     常用词 — excluded from 高频词 / 生造词. Sourced from
                           哈工大 (HIT) + 百度 (Baidu) stopword lists + 通用叙事词.
- ``surnames.txt``         百家姓 + 日文姓 — person-name exclusion (jieba 'nr'
                           + surname-first-char heuristic)
- ``translit_chars.txt``   音译字 — foreign-name (Alice→爱丽丝/艾丽丝/艾莉丝)
                           detection.

Each loader returns a cached ``frozenset`` (loaded once per process).
``append_words`` lets callers extend a list at runtime (self-maintenance),
mirroring ``dictionaries.append_to_genre_dict``.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

_ROOT = Path(__file__).resolve().parent / "resources" / "wordlists"

# Public list names → file (also the allowed targets for append_words).
_FILES = {
    "common_words": "common_words.txt",
    "surnames": "surnames.txt",
    "translit_chars": "translit_chars.txt",
}


def _read(path: Path) -> set[str]:
    if not path.exists():
        return set()
    out: set[str] = set()
    for line in path.read_text("utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        # one line may hold several whitespace/comma-separated words
        for tok in s.replace("，", " ").replace(",", " ").split():
            tok = tok.strip()
            if tok and not tok.startswith("#"):
                out.add(tok)
    return out


@lru_cache(maxsize=1)
def load_common_words() -> frozenset[str]:
    return frozenset(_read(_ROOT / _FILES["common_words"]))


@lru_cache(maxsize=1)
def load_surnames() -> frozenset[str]:
    return frozenset(_read(_ROOT / _FILES["surnames"]))


@lru_cache(maxsize=1)
def load_translit_chars() -> frozenset[str]:
    """音译字集合（单字）。"""
    chars: set[str] = set()
    for tok in _read(_ROOT / _FILES["translit_chars"]):
        chars.update(tok)   # split multi-char tokens into individual chars
    return frozenset(chars)


_LOADERS = {
    "common_words": load_common_words,
    "surnames": load_surnames,
    "translit_chars": load_translit_chars,
}


def append_words(list_name: str, words: list[str]) -> int:
    """Append words to a word-list file (dedup against existing). Returns
    how many were actually added. Invalidates the cache."""
    fname = _FILES.get(list_name)
    if not fname:
        raise ValueError(f"unknown word list: {list_name!r}")
    p = _ROOT / fname
    p.parent.mkdir(parents=True, exist_ok=True)
    existing = _read(p)
    added = [w.strip() for w in words
             if (w or "").strip() and w.strip() not in existing]
    if added:
        with p.open("a", encoding="utf-8") as f:
            f.write("\n# auto-appended\n" + "\n".join(added) + "\n")
        _LOADERS[list_name].cache_clear()
    return len(added)
