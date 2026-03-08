"""
Chapter Splitter — splits full-text works into chapters.

README §5.1 preprocessing/chapter_splitter.py:
  Detects chapter boundaries using Chinese chapter heading patterns.
"""
from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger("inkoctobot.preprocessing.chapter_splitter")

# Common Chinese chapter heading patterns
_CHAPTER_PATTERNS = [
    re.compile(r"^第[一二三四五六七八九十百千零\d]+[章回节卷][\s：:．.].*", re.MULTILINE),
    re.compile(r"^Chapter\s+\d+.*", re.MULTILINE | re.IGNORECASE),
    re.compile(r"^[（(]\d+[)）].*", re.MULTILINE),
    re.compile(r"^【.+?】", re.MULTILINE),
]


def split_chapters(
    text: str,
    *,
    min_chapter_length: int = 200,
    patterns: list[re.Pattern] | None = None,
) -> list[dict[str, Any]]:
    """
    Split full text into chapters.

    Returns list of {"chapter_num": int, "title": str, "content": str, "start": int}.
    """
    pats = patterns or _CHAPTER_PATTERNS

    # Collect all chapter heading positions
    headings: list[tuple[int, str]] = []
    for pat in pats:
        for m in pat.finditer(text):
            headings.append((m.start(), m.group().strip()))

    headings.sort(key=lambda x: x[0])

    # Deduplicate overlapping matches
    deduped: list[tuple[int, str]] = []
    for pos, title in headings:
        if not deduped or pos - deduped[-1][0] > 10:
            deduped.append((pos, title))
    headings = deduped

    if not headings:
        # No chapter headings found — treat entire text as one chapter
        return [{"chapter_num": 1, "title": "", "content": text.strip(), "start": 0}]

    chapters: list[dict[str, Any]] = []
    for i, (pos, title) in enumerate(headings):
        end = headings[i + 1][0] if i + 1 < len(headings) else len(text)
        content = text[pos:end].strip()
        # Remove heading from content body
        lines = content.split("\n", 1)
        body = lines[1].strip() if len(lines) > 1 else ""

        if len(body) >= min_chapter_length or not chapters:
            chapters.append({
                "chapter_num": len(chapters) + 1,
                "title": title,
                "content": body,
                "start": pos,
            })
        elif chapters:
            # Too short — merge with previous chapter
            chapters[-1]["content"] += "\n\n" + content

    logger.info("Split text into %d chapters", len(chapters))
    return chapters


def detect_chapter_pattern(text: str, sample_size: int = 5000) -> str:
    """Detect the dominant chapter heading pattern in a text sample."""
    sample = text[:sample_size]
    counts = {}
    for i, pat in enumerate(_CHAPTER_PATTERNS):
        matches = pat.findall(sample)
        if matches:
            counts[i] = len(matches)
    if not counts:
        return "none"
    best = max(counts, key=counts.get)
    labels = ["chinese_numbered", "english_numbered", "parenthesized", "bracketed"]
    return labels[best] if best < len(labels) else "unknown"
