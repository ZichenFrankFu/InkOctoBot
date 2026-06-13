"""Modular, user-extensible word-list config for opening-chapter NLP.

Bundled canonical lists live read-only under ``resources/wordlists/``:

- ``common_words.txt``     常用词 — excluded from 高频词 / 生造词. Sourced from
                           哈工大 (HIT) + 百度 (Baidu) stopword lists + 通用叙事词.
- ``surnames.txt``         百家姓 + 日文姓 — person-name exclusion (jieba 'nr'
                           + surname-first-char heuristic)
- ``translit_chars.txt``   音译字 — foreign-name (Alice→爱丽丝/艾丽丝/艾莉丝)
                           detection.

User edits — classifying a 高频词 as 人名/常用词 from the NLP panel, or the
市场特征提取页「资源管理」tab CRUD — must persist across restarts AND survive
the PyInstaller frozen bundle (whose resources dir is a read-only ``_MEIPASS``
temp). They are therefore written to a writable *overlay* under
``runtime_paths.data_dir()/wordlists/`` rather than to the bundled files:

    {name}.add.txt      user-added words (added on top of the bundled set)
    {name}.remove.txt   tombstones (hidden even if present in the bundle)

Effective list = (bundled ∪ adds) − removes. Each loader returns a cached
``frozenset`` (rebuilt after any edit via the shared cache invalidation).
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

_ROOT = Path(__file__).resolve().parent / "resources" / "wordlists"

# Public list names → bundled file.
_FILES = {
    "common_words": "common_words.txt",
    "surnames": "surnames.txt",
    "translit_chars": "translit_chars.txt",
}

# Human labels for the API / UI (资源管理 tab) and the targets a user may CRUD.
LIST_LABELS = {
    "common_words": "常用词",
    "surnames": "人名",
}


def _overlay_dir() -> Path:
    """Writable dir for user add/remove overlays. Honors an env override
    (tests) → runtime user data dir → bundled ``_user`` fallback (dev)."""
    env = os.environ.get("INKOCTOBOT_WORDLIST_DIR")
    if env:
        d = Path(env)
    else:
        try:
            from ...runtime_paths import data_dir
            d = data_dir() / "wordlists"
        except Exception:
            d = _ROOT / "_user"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _read_words(path: Path) -> set[str]:
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


def _bundled(list_name: str) -> set[str]:
    fname = _FILES.get(list_name)
    return _read_words(_ROOT / fname) if fname else set()


def _overlay(list_name: str, kind: str) -> set[str]:
    """kind ∈ {'add', 'remove'} — the user overlay for a list."""
    return _read_words(_overlay_dir() / f"{list_name}.{kind}.txt")


@lru_cache(maxsize=8)
def _effective(list_name: str) -> frozenset[str]:
    """(bundled ∪ user-adds) − user-removes, cached per process."""
    words = _bundled(list_name) | _overlay(list_name, "add")
    words -= _overlay(list_name, "remove")
    return frozenset(words)


def _invalidate() -> None:
    """Drop all word-list caches after an edit."""
    _effective.cache_clear()


# ── Loaders (back-compat names; now overlay-aware) ──

def load_common_words() -> frozenset[str]:
    return _effective("common_words")


def load_surnames() -> frozenset[str]:
    return _effective("surnames")


def load_translit_chars() -> frozenset[str]:
    """音译字集合（单字）。"""
    chars: set[str] = set()
    for tok in _effective("translit_chars"):
        chars.update(tok)   # split multi-char tokens into individual chars
    return frozenset(chars)


# ── User CRUD (resource management) ──

def _append_lines(path: Path, words: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write("\n".join(words) + "\n")


def _rewrite(path: Path, words: set[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(("\n".join(sorted(words)) + "\n") if words else "", "utf-8")


def add_word(list_name: str, word: str) -> bool:
    """Add ``word`` to a list's effective set (resource self-maintenance).
    Returns True if it was newly added. Clears any tombstone for it."""
    if list_name not in _FILES:
        raise ValueError(f"unknown word list: {list_name!r}")
    word = (word or "").strip()
    if not word:
        return False
    od = _overlay_dir()
    removed = _overlay(list_name, "remove")
    if word in removed:                       # un-tombstone
        removed.discard(word)
        _rewrite(od / f"{list_name}.remove.txt", removed)
    already = word in _effective(list_name)
    if word not in _overlay(list_name, "add"):
        _append_lines(od / f"{list_name}.add.txt", [word])
    _invalidate()
    return not already


def remove_word(list_name: str, word: str) -> bool:
    """Remove ``word`` from a list's effective set. Drops it from the user
    add-overlay and, if it's a bundled word, writes a tombstone so it stays
    hidden. Returns True if it was effectively present."""
    if list_name not in _FILES:
        raise ValueError(f"unknown word list: {list_name!r}")
    word = (word or "").strip()
    if not word:
        return False
    od = _overlay_dir()
    was_present = word in _effective(list_name)
    adds = _overlay(list_name, "add")
    if word in adds:
        adds.discard(word)
        _rewrite(od / f"{list_name}.add.txt", adds)
    if word in _bundled(list_name):           # tombstone bundled words
        removed = _overlay(list_name, "remove")
        removed.add(word)
        _rewrite(od / f"{list_name}.remove.txt", removed)
    _invalidate()
    return was_present


def append_words(list_name: str, words: list[str]) -> int:
    """Bulk add (dedup against the effective set). Returns how many were
    newly added. Used by self-maintenance pipelines."""
    n = 0
    for w in words or []:
        if add_word(list_name, w):
            n += 1
    return n


def list_words(list_name: str, q: str | None = None, limit: int = 1000) -> dict:
    """Effective list for the 资源管理 tab: sorted words (optional substring
    filter ``q``), each flagged ``user`` (user-added, i.e. removable cleanly)
    vs bundled. Returns ``{total, items:[{word, user}], truncated}``."""
    if list_name not in _FILES:
        raise ValueError(f"unknown word list: {list_name!r}")
    eff = _effective(list_name)
    adds = _overlay(list_name, "add")
    q = (q or "").strip()
    words = sorted(w for w in eff if (not q or q in w))
    total = len(words)
    items = [{"word": w, "user": w in adds} for w in words[:limit]]
    return {"total": total, "items": items, "truncated": total > limit}
