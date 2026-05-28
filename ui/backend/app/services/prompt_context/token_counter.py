"""Token counting for builder diagnostics.

Wraps tiktoken when available, falls back to a CJK-aware heuristic
when it isn't. Single class, single global singleton — no
provider-specific subclasses, no pricing, no cost estimation. Just a
``count(text) -> int`` that the builder calls for telemetry.

Heuristic formula (no tiktoken):
- CJK (U+4E00–U+9FFF):  1.6 tokens / char
- everything else:      0.25 tokens / char

For Claude tokenizer this is approximate (±5–10%); for GPT cl100k_base
it's exact when tiktoken loads.
"""
from __future__ import annotations

import logging
import threading
from functools import lru_cache
from typing import Literal

logger = logging.getLogger("inkoctobot.services.prompt_context.token_counter")


Method = Literal["tiktoken", "heuristic"]


_CJK_LO = "一"
_CJK_HI = "鿿"


def _heuristic_count(text: str) -> int:
    """CJK-aware fallback. Used when tiktoken isn't importable.

    1.6 tokens per CJK character + 0.25 tokens per other character.
    Matches GPT-4 cl100k_base behavior within ~10%; close enough for
    diagnostics (which round to integers anyway).
    """
    if not text:
        return 0
    chinese = sum(1 for ch in text if _CJK_LO <= ch <= _CJK_HI)
    other = len(text) - chinese
    return int(chinese * 1.6 + other * 0.25)


class TokenCounter:
    """Process-global token counter. Construct via :func:`get_token_counter`."""

    def __init__(self) -> None:
        self._method: Method = "heuristic"
        self._encoder = None
        try:
            import tiktoken  # noqa: WPS433
            self._encoder = tiktoken.get_encoding("cl100k_base")
            self._method = "tiktoken"
        except Exception as e:
            logger.debug("tiktoken unavailable, using heuristic: %s", e)

        # LRU cache on a bound method — wrap the inner counter once at
        # construction so the cache is per-instance (lets tests use
        # ``reset_for_tests`` to wipe it without touching other state).
        self._count_impl = lru_cache(maxsize=1024)(self._count_uncached)

    def get_method(self) -> Method:
        return self._method

    def count(self, text: str) -> int:
        if not text:
            return 0
        return self._count_impl(text)

    def count_batch(self, texts: list[str]) -> list[int]:
        return [self.count(t) for t in texts]

    # ─────────── internals ───────────

    def _count_uncached(self, text: str) -> int:
        if self._method == "tiktoken" and self._encoder is not None:
            try:
                return len(self._encoder.encode(text))
            except Exception as e:
                # Should be near-impossible with cl100k_base, but if a
                # text breaks the tokenizer (e.g. a sequence of bytes
                # that fails to decode), don't crash the build.
                logger.debug("tiktoken encode failed, falling back: %s", e)
        return _heuristic_count(text)


_singleton: TokenCounter | None = None
_singleton_lock = threading.Lock()


def get_token_counter() -> TokenCounter:
    """Process-global TokenCounter accessor."""
    global _singleton
    if _singleton is None:
        with _singleton_lock:
            if _singleton is None:
                _singleton = TokenCounter()
    return _singleton


def reset_for_tests() -> None:
    """Drop the singleton so the next call rebuilds (used by tests that
    mock tiktoken availability)."""
    global _singleton
    with _singleton_lock:
        _singleton = None
