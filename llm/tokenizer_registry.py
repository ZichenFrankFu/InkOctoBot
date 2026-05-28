"""Per-model tokenizer dispatch.

``count_tokens(provider, model, text) -> int`` picks the right tokenizer
for every (provider, model) pair the project uses:

- ``openai`` / ``deepseek``     — tiktoken cl100k_base (DeepSeek is
                                  OpenAI-compatible at the tokenizer
                                  level)
- ``anthropic`` (Claude)        — cl100k_base * 1.15 calibration
                                  (Anthropic ships no public tokenizer)
- ``gemini``                    — cl100k_base * 1.10 calibration
- ``ollama`` / ``vllm``         — HF AutoTokenizer for the wrapped model
                                  name (lazy-loaded; cached)
- everything else / load fail   — CJK heuristic (1.6 / 0.25 split)

All tokenizer instances are LRU-cached so repeated calls don't pay the
load cost. Falls back gracefully when a tokenizer can't be loaded —
no LLM call should ever break because the tokenizer wasn't available.
"""
from __future__ import annotations

import logging
import threading
from functools import lru_cache
from typing import Protocol


logger = logging.getLogger("inkoctobot.llm.tokenizer_registry")


_CALIBRATION: dict[str, float] = {
    "anthropic": 1.15,
    "gemini":    1.10,
}


class _TokenCounter(Protocol):
    def count(self, text: str) -> int: ...


# ─────────── implementations ───────────


class _Tiktoken:
    """OpenAI-compatible cl100k_base counter, optionally calibrated for
    other vendors whose tokenizers are close but not identical."""

    def __init__(self, calibration: float = 1.0) -> None:
        self._calibration = calibration
        self._enc = None
        try:
            import tiktoken
            self._enc = tiktoken.get_encoding("cl100k_base")
        except Exception as e:
            logger.debug("tiktoken unavailable: %s", e)

    def count(self, text: str) -> int:
        if not text:
            return 0
        if self._enc is not None:
            try:
                n = len(self._enc.encode(text))
            except Exception:
                n = _heuristic_count(text)
        else:
            n = _heuristic_count(text)
        return int(n * self._calibration)


class _HFTokenizer:
    """Hugging Face AutoTokenizer wrapper for local models (Qwen,
    DeepSeek's open weights, etc.). Lazy-loaded; failure → heuristic."""

    def __init__(self, hf_name: str) -> None:
        self._hf_name = hf_name
        self._tok = None
        self._tried = False

    def _ensure(self) -> None:
        if self._tried:
            return
        self._tried = True
        try:
            from transformers import AutoTokenizer
            self._tok = AutoTokenizer.from_pretrained(self._hf_name)
            logger.info("HF tokenizer loaded: %s", self._hf_name)
        except Exception as e:
            logger.debug("HF tokenizer %s unavailable: %s", self._hf_name, e)

    def count(self, text: str) -> int:
        if not text:
            return 0
        self._ensure()
        if self._tok is None:
            return _heuristic_count(text)
        try:
            return len(self._tok.encode(text, add_special_tokens=False))
        except Exception:
            return _heuristic_count(text)


class _Heuristic:
    """CJK-aware fallback: 1.6 tokens/CJK char + 0.25 tokens/other."""

    def count(self, text: str) -> int:
        return _heuristic_count(text)


def _heuristic_count(text: str) -> int:
    if not text:
        return 0
    chinese = sum(1 for ch in text if "一" <= ch <= "鿿")
    other = len(text) - chinese
    return int(chinese * 1.6 + other * 0.25)


# ─────────── HF name mapping for local model wrappers ───────────


_OLLAMA_TO_HF: dict[str, str] = {
    "qwen2.5":            "Qwen/Qwen2.5-7B-Instruct",
    "qwen2.5:14b":        "Qwen/Qwen2.5-14B-Instruct",
    "qwen2.5:32b":        "Qwen/Qwen2.5-32B-Instruct",
    "qwen2.5:72b":        "Qwen/Qwen2.5-72B-Instruct",
    "qwen3:8b":           "Qwen/Qwen3-8B",
    "qwen3:32b":          "Qwen/Qwen3-32B",
    "deepseek-v3":        "deepseek-ai/DeepSeek-V3",
    "deepseek-coder-v3":  "deepseek-ai/deepseek-coder-V3",
    "llama3":             "meta-llama/Meta-Llama-3-8B",
    "llama3.1":           "meta-llama/Meta-Llama-3.1-8B-Instruct",
    "mistral":            "mistralai/Mistral-7B-Instruct-v0.3",
}


def _resolve_hf_name(model: str) -> str:
    """Map ollama/vllm model identifier → HF repo. Strips ``:tag``
    suffix when no exact match, then falls back to the model string."""
    m = (model or "").lower()
    if m in _OLLAMA_TO_HF:
        return _OLLAMA_TO_HF[m]
    base = m.split(":", 1)[0]
    if base in _OLLAMA_TO_HF:
        return _OLLAMA_TO_HF[base]
    # Best-effort: user might have supplied a HF repo path directly.
    return model


# ─────────── public surface ───────────


_cache: dict[tuple[str, str], _TokenCounter] = {}
_lock = threading.Lock()


def _make_counter(provider: str, model: str) -> _TokenCounter:
    p = (provider or "").lower()
    if p in ("openai", "deepseek", "volcengine", "baidu_qianfan",
              "aliyun_bailian", "grok", "mock"):
        return _Tiktoken()
    if p == "anthropic":
        return _Tiktoken(calibration=_CALIBRATION["anthropic"])
    if p == "gemini":
        return _Tiktoken(calibration=_CALIBRATION["gemini"])
    if p in ("ollama", "vllm"):
        return _HFTokenizer(_resolve_hf_name(model))
    return _Heuristic()


def get_tokenizer(provider: str, model: str) -> _TokenCounter:
    """Process-cached tokenizer for (provider, model). Always returns a
    counter — never raises. Heuristic fallback on any failure."""
    key = ((provider or "").lower(), (model or "").lower())
    if key in _cache:
        return _cache[key]
    with _lock:
        if key not in _cache:
            try:
                _cache[key] = _make_counter(*key)
            except Exception as e:
                logger.warning(
                    "failed to build tokenizer for %s/%s: %s — using heuristic",
                    provider, model, e,
                )
                _cache[key] = _Heuristic()
    return _cache[key]


def count_tokens(provider: str, model: str, text: str) -> int:
    """One-shot count via the cached tokenizer."""
    return get_tokenizer(provider, model).count(text)


def reset_for_tests() -> None:
    with _lock:
        _cache.clear()
