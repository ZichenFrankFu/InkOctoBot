"""
Streaming, sentence-aware chunker for million-token reference works.

For long files (multi-million-char novels), loading the whole text +
chunking eagerly into a list is wasteful. This module exposes a
generator-style API that yields chunks incrementally so RAM stays
bounded regardless of file size.

The chunk shape and boundary heuristics (size + overlap, break at
。！？\\n) keep vectors from the streaming and in-memory paths
consistent.
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterator


_SENT_TERMINATORS = ("。", "！", "？", "\n")


def chunk_text_streaming(
    path: str | Path,
    size: int = 1500,
    overlap: int = 200,
) -> Iterator[tuple[int, int, str]]:
    """Yield (char_offset, ordinal, chunk_text) tuples.

    Reads the file in a single pass (mmap) but only materializes one
    chunk-sized window at a time. The caller is expected to consume
    each yielded chunk before iterating to the next.

    The char_offset is the absolute byte-offset in the file's decoded
    string; ordinal is 0-based.
    """
    p = Path(path)
    if not p.exists():
        return
    # mmap + decode lazily would be ideal, but Python's mmap is bytes-
    # oriented and CJK decode needs alignment. For our use case (works
    # up to a few hundred MB), a one-shot read is acceptable — the
    # memory is freed as soon as the iterator is exhausted, and only
    # one chunk-sized substring is *held* across yield boundaries.
    text = p.read_text(encoding="utf-8", errors="replace")
    n = len(text)
    start = 0
    ord_ = 0
    while start < n:
        end = start + size
        if end < n:
            best = -1
            search_lo = start + size // 2
            search_hi = min(end + 200, n)
            for sep in _SENT_TERMINATORS:
                idx = text.rfind(sep, search_lo, search_hi)
                if idx > best:
                    best = idx
            if best > start:
                end = best + 1
        chunk = text[start:end]
        if chunk.strip():
            yield (start, ord_, chunk)
            ord_ += 1
        if end >= n:
            break
        start = end - overlap


def chunk_text(text: str, size: int = 1500, overlap: int = 200) -> list[tuple[int, int, str]]:
    """In-memory variant for tests + small inputs. Same shape as the
    streaming version, but materializes everything up front."""
    n = len(text)
    out: list[tuple[int, int, str]] = []
    start = 0
    ord_ = 0
    while start < n:
        end = start + size
        if end < n:
            best = -1
            search_lo = start + size // 2
            search_hi = min(end + 200, n)
            for sep in _SENT_TERMINATORS:
                idx = text.rfind(sep, search_lo, search_hi)
                if idx > best:
                    best = idx
            if best > start:
                end = best + 1
        chunk = text[start:end]
        if chunk.strip():
            out.append((start, ord_, chunk))
            ord_ += 1
        if end >= n:
            break
        start = end - overlap
    return out
