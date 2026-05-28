"""Pull + clean the first N chapters of a work from the crawler DB.

Cleans common cruft (求收藏 / 求推荐 / 未完待续 / etc) so downstream
NLP and LLM extractors see narrative-only text.
"""
from __future__ import annotations

import logging
import re
import sqlite3
from pathlib import Path

logger = logging.getLogger("inkoctobot.market_extractor.chapter_fetcher")


# Patterns that get stripped before downstream analysis sees the text.
# Each is a regex; matched lines are dropped entirely.
_LINE_NOISE_PATTERNS = [
    re.compile(r"^\s*求(?:收藏|推荐|月票|订阅)"),
    re.compile(r"^\s*PS[:：]"),
    re.compile(r"^\s*作者[有的]?话"),
    re.compile(r"^\s*(?:未完待续|待续|下一?章预告)"),
    re.compile(r"^\s*\([^)]*\)\s*$"),     # standalone "(广告)"
    re.compile(r"^\s*【[^】]*】\s*$"),       # standalone 【广告】
    re.compile(r"^\s*[—\-=]+\s*$"),         # separator lines
]


def clean_chapter_text(raw: str) -> str:
    """Strip ads / separators / author notes; collapse blank lines."""
    if not raw:
        return ""
    out: list[str] = []
    for line in raw.replace("\r\n", "\n").split("\n"):
        stripped = line.strip()
        if not stripped:
            # collapse runs of blank lines to one
            if out and out[-1] != "":
                out.append("")
            continue
        if any(p.search(stripped) for p in _LINE_NOISE_PATTERNS):
            continue
        out.append(line)
    # Trim trailing blanks.
    while out and out[-1] == "":
        out.pop()
    return "\n".join(out)


def fetch_chapter(
    crawler_db: str, novel_id: str, chapter_num: int,
) -> str | None:
    """Return cleaned chapter text or None if unavailable."""
    if not Path(crawler_db).exists():
        return None
    try:
        with sqlite3.connect(crawler_db) as con:
            con.row_factory = sqlite3.Row
            row = con.execute(
                "SELECT content FROM chapters "
                "WHERE novel_id = ? AND chapter_num = ?",
                (novel_id, chapter_num),
            ).fetchone()
    except sqlite3.OperationalError as e:
        logger.warning("crawler chapters schema mismatch: %s", e)
        return None
    if not row or not row["content"]:
        return None
    return clean_chapter_text(row["content"])


def fetch_first_n_chapters(
    crawler_db: str, novel_id: str, n: int = 5,
) -> dict[int, str]:
    """Return ``{chapter_num: cleaned_text}`` for chapters 1..n that
    are present + non-empty. Missing chapters are silently skipped."""
    out: dict[int, str] = {}
    for k in range(1, n + 1):
        text = fetch_chapter(crawler_db, novel_id, k)
        if text:
            out[k] = text
    return out


def cache_chapter(
    cache_root: str, work_id: str, chapter_num: int, text: str,
) -> str:
    """Write cleaned text to ``<cache_root>/<work_id>/ch_<N>.txt``.
    Returns the file path. Caller decides when to invalidate."""
    work_dir = Path(cache_root) / work_id
    work_dir.mkdir(parents=True, exist_ok=True)
    p = work_dir / f"ch_{chapter_num:03d}.txt"
    p.write_text(text, encoding="utf-8")
    return str(p)


def read_cached_chapter(
    cache_root: str, work_id: str, chapter_num: int,
) -> str | None:
    p = Path(cache_root) / work_id / f"ch_{chapter_num:03d}.txt"
    if not p.exists():
        return None
    return p.read_text("utf-8")
