"""Modular word-list config for opening-chapter NLP (高频词 / 生造词).

Externalizes the static vocabularies that were hardcoded in
``opening_stats.py`` into editable resource files under
``resources/wordlists/`` so they're proper config, not code:

- ``stopwords.txt``        常用词 / 停用词 — excluded from 高频词 / 生造词
- ``surnames.txt``         百家姓 — drives person-name exclusion (jieba 'nr'
                           + surname-first-char heuristic)
- ``generic_content.txt``  通用叙事词 — no platform/genre signal

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
    "stopwords": "stopwords.txt",
    "surnames": "surnames.txt",
    "generic_content": "generic_content.txt",
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
def load_stopwords() -> frozenset[str]:
    return frozenset(_read(_ROOT / _FILES["stopwords"]))


@lru_cache(maxsize=1)
def load_surnames() -> frozenset[str]:
    return frozenset(_read(_ROOT / _FILES["surnames"]))


@lru_cache(maxsize=1)
def load_generic_content() -> frozenset[str]:
    return frozenset(_read(_ROOT / _FILES["generic_content"]))


_LOADERS = {
    "stopwords": load_stopwords,
    "surnames": load_surnames,
    "generic_content": load_generic_content,
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
