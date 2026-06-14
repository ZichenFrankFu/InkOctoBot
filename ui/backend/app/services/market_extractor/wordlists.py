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
import re
from functools import lru_cache
from pathlib import Path

_ROOT = Path(__file__).resolve().parent / "resources" / "wordlists"

# Public list names → bundled file.
_FILES = {
    "common_words": "common_words.txt",
    "surnames": "surnames.txt",
    "given_names": "given_names.txt",
    "name_chars": "name_chars.txt",
    "translit_chars": "translit_chars.txt",
}

# Human labels for the API / UI (资源管理 tab) and the targets a user may CRUD.
LIST_LABELS = {
    "common_words": "常用词",
    "surnames": "姓",
    "given_names": "名",
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


def _add_files(list_name: str) -> list[tuple[Path, str]]:
    """User add-overlay files for a list: the bare ``{list}.add.txt`` plus the
    country-tagged ``{list}.{group}.add.txt`` (so adds from 中文·姓氏 etc. land
    back in that section). Returns (path, group) — group="" for the bare file."""
    od = _overlay_dir()
    out: list[tuple[Path, str]] = []
    for p in sorted(od.glob(f"{list_name}.*add.txt")):
        n = p.name
        if n == f"{list_name}.add.txt":
            out.append((p, ""))
        elif n.endswith(".add.txt"):
            out.append((p, n[len(list_name) + 1:-len(".add.txt")]))
    return out


def _overlay(list_name: str, kind: str) -> set[str]:
    """kind ∈ {'add', 'remove'} — the user overlay for a list. ``add`` unions
    the bare + all country-tagged add files."""
    if kind == "add":
        words: set[str] = set()
        for p, _g in _add_files(list_name):
            words |= _read_words(p)
        return words
    return _read_words(_overlay_dir() / f"{list_name}.{kind}.txt")


def _add_groups(list_name: str) -> dict[str, str]:
    """word → 国别 group from the country-tagged add files（用户添加项的国别）。"""
    out: dict[str, str] = {}
    for p, g in _add_files(list_name):
        if not g:
            continue
        for w in _read_words(p):
            out.setdefault(w, g)
    return out


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


def load_given_names() -> frozenset[str]:
    """名（given names）— 中文/西方/日本，含用户 overlay。"""
    return _effective("given_names")


def load_name_chars() -> frozenset[str]:
    """中文名字常用字（单字）— 人名识别辅助，非 CRUD 资源。"""
    chars: set[str] = set()
    for tok in _effective("name_chars"):
        chars.update(tok)
    return frozenset(chars)


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


def add_word(list_name: str, word: str, group: str | None = None) -> bool:
    """Add ``word`` to a list's effective set. ``group`` (国别：中文/日本/西方)
    tags the add so it shows in that section. Returns True if newly added."""
    if list_name not in _FILES:
        raise ValueError(f"unknown word list: {list_name!r}")
    word = (word or "").strip()
    group = (group or "").strip()
    if not word:
        return False
    od = _overlay_dir()
    removed = _overlay(list_name, "remove")
    if word in removed:                       # un-tombstone
        removed.discard(word)
        _rewrite(od / f"{list_name}.remove.txt", removed)
    already = word in _effective(list_name)
    if word not in _overlay(list_name, "add"):
        fname = f"{list_name}.{group}.add.txt" if group else f"{list_name}.add.txt"
        _append_lines(od / fname, [word])
    _invalidate()
    return not already


def remove_word(list_name: str, word: str) -> bool:
    """Remove ``word`` from a list's effective set. Drops it from every user
    add-overlay (含国别文件) and, if bundled, writes a tombstone."""
    if list_name not in _FILES:
        raise ValueError(f"unknown word list: {list_name!r}")
    word = (word or "").strip()
    if not word:
        return False
    od = _overlay_dir()
    was_present = word in _effective(list_name)
    for p, _g in _add_files(list_name):       # drop from any add file
        ws = _read_words(p)
        if word in ws:
            ws.discard(word)
            _rewrite(p, ws)
    if word in _bundled(list_name):           # tombstone bundled words
        removed = _overlay(list_name, "remove")
        removed.add(word)
        _rewrite(od / f"{list_name}.remove.txt", removed)
    _invalidate()
    return was_present


def append_words(list_name: str, words: list[str], group: str | None = None) -> int:
    """Bulk add (dedup against the effective set). Returns how many were
    newly added. Used by self-maintenance pipelines."""
    n = 0
    for w in words or []:
        if add_word(list_name, w, group):
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


def _grouped_bundled(list_name: str) -> dict[str, str]:
    """word → group, parsed from ``# @group:<名称>`` markers in the bundled
    file (人名按国家分组：中国 / 日本…）. Words before any marker → 内置。"""
    fname = _FILES.get(list_name)
    if not fname:
        return {}
    p = _ROOT / fname
    if not p.exists():
        return {}
    word_group: dict[str, str] = {}
    cur = "内置"
    for line in p.read_text("utf-8").splitlines():
        s = line.strip()
        if not s:
            continue
        if s.startswith("#"):
            m = re.search(r"@group:\s*(\S+)", s)
            if m:
                cur = m.group(1)
            continue
        for tok in s.replace("，", " ").replace(",", " ").split():
            tok = tok.strip()
            if tok and not tok.startswith("#"):
                word_group.setdefault(tok, cur)
    return word_group


_GROUP_ORDER = ("中国", "日本", "用户添加", "内置", "其他")


def list_grouped(list_name: str, q: str | None = None) -> dict:
    """Effective list grouped (人名按国家分组 + 用户添加)，供资源管理 tab 折叠
    展示。Returns ``{list, groups:[{group, items:[{word,user}]}], total}``."""
    if list_name not in _FILES:
        raise ValueError(f"unknown word list: {list_name!r}")
    eff = _effective(list_name)
    adds = _overlay(list_name, "add")
    word_group = _grouped_bundled(list_name)
    q = (q or "").strip()
    buckets: dict[str, list[dict]] = {}
    for w in sorted(eff):
        if q and q not in w:
            continue
        g = word_group.get(w) or ("用户添加" if w in adds else "内置")
        buckets.setdefault(g, []).append({"word": w, "user": w in adds})
    ordered = [g for g in _GROUP_ORDER if g in buckets]
    ordered += [g for g in buckets if g not in _GROUP_ORDER]
    groups = [{"group": g, "items": buckets[g]} for g in ordered]
    return {"list": list_name, "groups": groups,
            "total": sum(len(b) for b in buckets.values())}


_NAME_COUNTRIES = ("中文", "日本", "西方")


def names_overview(q: str | None = None) -> dict:
    """人名总览，按 section 返回 —— 每个 section 自带「往哪个 list+国别 添加」
    （addList/addGroup）。section 列表：

        中文·姓氏 / 中文·名字 / 日本·姓氏 / 日本·名字 / 西方（姓名不分） / 用户添加

    西方不分姓/名（中文用户对西方姓名不敏感），合并 surnames+given 的西方项。
    每项 item 自带 ``list`` 以便删除时定位。"""
    q = (q or "").strip()

    def buckets(list_name: str) -> dict[str, list]:
        """国别 → items（含国别 user-add 的归位）。"""
        eff = _effective(list_name)
        adds = _overlay(list_name, "add")
        w2g_b = _grouped_bundled(list_name)
        w2g_u = _add_groups(list_name)
        out: dict[str, list] = {}
        for w in sorted(eff):
            if q and q not in w:
                continue
            g = w2g_b.get(w) or w2g_u.get(w) or "用户添加"
            out.setdefault(g, []).append(
                {"word": w, "user": w in adds, "list": list_name})
        return out

    sb = buckets("surnames")
    gb = buckets("given_names")
    sections = [
        {"key": "中文·姓氏", "title": "中文 · 姓氏",
         "addList": "surnames", "addGroup": "中文", "items": sb.get("中文", [])},
        {"key": "中文·名字", "title": "中文 · 名字",
         "addList": "given_names", "addGroup": "中文", "items": gb.get("中文", [])},
        {"key": "日本·姓氏", "title": "日本 · 姓氏",
         "addList": "surnames", "addGroup": "日本", "items": sb.get("日本", [])},
        {"key": "日本·名字", "title": "日本 · 名字",
         "addList": "given_names", "addGroup": "日本", "items": gb.get("日本", [])},
    ]
    west = sorted(sb.get("西方", []) + gb.get("西方", []), key=lambda x: x["word"])
    sections.append({"key": "西方", "title": "西方姓名",
                     "addList": "given_names", "addGroup": "西方", "items": west})
    ua = sorted(sb.get("用户添加", []) + gb.get("用户添加", []), key=lambda x: x["word"])
    if ua:
        sections.append({"key": "用户添加", "title": "用户添加",
                         "addList": None, "addGroup": None, "items": ua})
    return {"sections": sections}


def export_text(list_name: str) -> str:
    """Effective list as a plain-text doc (one word per line) for 导出。"""
    if list_name not in _FILES:
        raise ValueError(f"unknown word list: {list_name!r}")
    return "\n".join(sorted(_effective(list_name))) + "\n"


def import_text(list_name: str, content: str, group: str | None = None) -> int:
    """Add every whitespace/comma-separated token from an uploaded txt
    (导入). Comment (#) lines ignored. Returns how many were newly added."""
    words: list[str] = []
    for line in (content or "").splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        for tok in s.replace("，", " ").replace(",", " ").split():
            tok = tok.strip()
            if tok and not tok.startswith("#"):
                words.append(tok)
    return append_words(list_name, words, group)
