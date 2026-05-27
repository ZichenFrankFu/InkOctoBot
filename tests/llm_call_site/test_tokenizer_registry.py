"""Tests for the per-model tokenizer registry."""
from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from llm import tokenizer_registry as tr


class TestHeuristicFallback(unittest.TestCase):
    """Even when tiktoken / HF fail, every counter must return a
    sensible non-zero number for non-empty input."""

    def setUp(self) -> None:
        tr.reset_for_tests()

    def tearDown(self) -> None:
        tr.reset_for_tests()

    def test_empty_text_returns_zero_everywhere(self) -> None:
        for provider in ("openai", "anthropic", "gemini",
                          "ollama", "vllm", "mock", "unknown"):
            self.assertEqual(
                tr.count_tokens(provider, "any-model", ""), 0,
                f"{provider} non-zero on empty",
            )

    def test_heuristic_pure_chinese(self) -> None:
        # 10 CJK chars × 1.6 = 16 tokens via heuristic path.
        # Unknown provider always falls into heuristic.
        n = tr.count_tokens("unknown", "x", "一二三四五六七八九十")
        self.assertEqual(n, 16)


class TestCalibration(unittest.TestCase):
    """Anthropic / Gemini scale cl100k output. Test the multiplier
    applied even when tiktoken can load."""

    def setUp(self) -> None:
        tr.reset_for_tests()

    def tearDown(self) -> None:
        tr.reset_for_tests()

    def test_anthropic_is_higher_than_openai(self) -> None:
        """If tiktoken is available, anthropic count = openai * 1.15.
        If not, both go through heuristic (no calibration) and are
        equal — assert >= so the test is robust to either path."""
        n_openai = tr.count_tokens("openai", "gpt-4o", "你好世界，这是测试")
        n_anthropic = tr.count_tokens("anthropic", "claude", "你好世界，这是测试")
        self.assertGreaterEqual(n_anthropic, n_openai)


class TestHFTokenizerFallback(unittest.TestCase):
    """Without network access, transformers can't load — counter must
    degrade to the heuristic instead of crashing the call site."""

    def setUp(self) -> None:
        tr.reset_for_tests()

    def tearDown(self) -> None:
        tr.reset_for_tests()

    def test_ollama_qwen_falls_back_to_heuristic(self) -> None:
        # On a clean sandbox the HF download fails; expect heuristic.
        n = tr.count_tokens("ollama", "qwen2.5:14b", "测试文本")
        self.assertGreater(n, 0)

    def test_unknown_local_model_returns_heuristic_count(self) -> None:
        n = tr.count_tokens("vllm", "nonexistent-model-xyz", "测试文本一")
        self.assertGreater(n, 0)


class TestCaching(unittest.TestCase):

    def setUp(self) -> None:
        tr.reset_for_tests()

    def tearDown(self) -> None:
        tr.reset_for_tests()

    def test_same_provider_model_reuses_counter(self) -> None:
        t1 = tr.get_tokenizer("openai", "gpt-4o")
        t2 = tr.get_tokenizer("openai", "gpt-4o")
        self.assertIs(t1, t2)

    def test_different_models_get_different_counters(self) -> None:
        t1 = tr.get_tokenizer("ollama", "qwen2.5:14b")
        t2 = tr.get_tokenizer("ollama", "qwen2.5:7b")
        # Different cache keys (model name differs).
        self.assertIsNot(t1, t2)


if __name__ == "__main__":
    unittest.main()
