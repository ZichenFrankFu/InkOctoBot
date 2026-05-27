"""Cached loaders for the genre / classical / slang / idiom dictionaries.

The files live in ``data/dictionaries/``. Each loader returns a
frozen ``set[str]`` — fast O(1) lookup, immutable per process.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path


_DICT_ROOT = Path(__file__).resolve().parent / "resources" / "dictionaries"


def _read_word_file(path: Path) -> set[str]:
    if not path.exists():
        return set()
    out: set[str] = set()
    for line in path.read_text("utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        out.add(s)
    return out


@lru_cache(maxsize=32)
def load_genre_dict(category: str) -> frozenset[str]:
    """Per-genre vocabulary. Returns empty set if the genre has no
    seed dictionary yet — caller should treat as "no filter applied"."""
    safe = "".join(c for c in (category or "").lower() if c.isalnum() or c in "-_")
    p = _DICT_ROOT / "genre" / f"{safe}.txt"
    return frozenset(_read_word_file(p))


@lru_cache(maxsize=1)
def load_classical_words() -> frozenset[str]:
    return frozenset(_read_word_file(_DICT_ROOT / "classical_words.txt"))


@lru_cache(maxsize=1)
def load_internet_slang() -> frozenset[str]:
    return frozenset(_read_word_file(_DICT_ROOT / "internet_slang.txt"))


@lru_cache(maxsize=1)
def load_idioms() -> frozenset[str]:
    return frozenset(_read_word_file(_DICT_ROOT / "idioms.txt"))


def append_to_genre_dict(category: str, new_words: list[str]) -> int:
    """Append words to the genre dict (Phase 4 self-maintenance).
    Returns how many actually got added (deduplicates against existing)."""
    safe = "".join(c for c in (category or "").lower() if c.isalnum() or c in "-_")
    p = _DICT_ROOT / "genre" / f"{safe}.txt"
    p.parent.mkdir(parents=True, exist_ok=True)
    existing = _read_word_file(p)
    added: list[str] = []
    for w in new_words:
        w = (w or "").strip()
        if not w or w in existing:
            continue
        existing.add(w)
        added.append(w)
    if added:
        with p.open("a", encoding="utf-8") as f:
            f.write("\n# auto-appended by Phase 4 aggregator\n")
            for w in added:
                f.write(w + "\n")
    # Invalidate the cache so the next load_genre_dict() sees the updates.
    load_genre_dict.cache_clear()
    return len(added)
