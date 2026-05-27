"""Tests for TokenCounter (Builder Diagnostics MVP)."""
from __future__ import annotations

import os
import sys
import types
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from ui.backend.app.services.prompt_context import token_counter
from ui.backend.app.services.prompt_context.token_counter import (
    TokenCounter, _heuristic_count, get_token_counter, reset_for_tests,
)


class TestHeuristic(unittest.TestCase):
    """Heuristic path runs even when tiktoken is present — used as the
    fallback. Counted by formula: 1.6 × CJK_chars + 0.25 × other_chars."""

    def test_empty(self) -> None:
        self.assertEqual(_heuristic_count(""), 0)

    def test_pure_chinese(self) -> None:
        # 10 Chinese chars → 1.6 × 10 = 16 tokens.
        self.assertEqual(_heuristic_count("一二三四五六七八九十"), 16)

    def test_pure_english(self) -> None:
        # 12 ASCII chars → 0.25 × 12 = 3 tokens.
        self.assertEqual(_heuristic_count("hello world!"), 3)

    def test_mixed(self) -> None:
        # 4 Chinese (6.4) + 6 ASCII (1.5) = 7.9 → int() = 7.
        self.assertEqual(_heuristic_count("你好世界 test!"), 7)


class TestCounterEitherPath(unittest.TestCase):
    """Contract tests that hold regardless of whether tiktoken can be
    loaded — i.e. work on both the tiktoken and heuristic paths. The
    sandbox can't pull the cl100k_base encoding from the network so the
    real CI may exercise either path."""

    def setUp(self) -> None:
        reset_for_tests()

    def tearDown(self) -> None:
        reset_for_tests()

    def test_method_is_one_of_two(self) -> None:
        counter = TokenCounter()
        self.assertIn(counter.get_method(), ("tiktoken", "heuristic"))

    def test_count_chinese_text(self) -> None:
        counter = TokenCounter()
        # Both paths should agree to within an order of magnitude —
        # tiktoken ≈ 2 tokens/char, heuristic = 1.6 tokens/char.
        n = counter.count("你好世界，欢迎来到测试")
        self.assertGreater(n, 5)
        self.assertLess(n, 50)

    def test_count_english_text(self) -> None:
        counter = TokenCounter()
        # English: tiktoken ≈ 2 tokens, heuristic = int(11 * 0.25) = 2.
        self.assertGreaterEqual(counter.count("hello world"), 2)
        self.assertLessEqual(counter.count("hello world"), 4)

    def test_count_mixed_text(self) -> None:
        counter = TokenCounter()
        n = counter.count("hello 你好 world 世界")
        self.assertGreater(n, 5)

    def test_count_empty_returns_zero(self) -> None:
        self.assertEqual(TokenCounter().count(""), 0)

    def test_count_batch(self) -> None:
        counter = TokenCounter()
        out = counter.count_batch(["hello", "你好"])
        self.assertEqual(len(out), 2)
        self.assertTrue(all(isinstance(v, int) for v in out))

    def test_cache_hit_returns_same_value(self) -> None:
        """Same input → same output, exercised via cached path."""
        counter = TokenCounter()
        first = counter.count("repeat me 一二三")
        second = counter.count("repeat me 一二三")
        self.assertEqual(first, second)
        info = counter._count_impl.cache_info()
        self.assertGreaterEqual(info.hits, 1)


class TestCounterHeuristicFallback(unittest.TestCase):
    """When tiktoken can't import, counter quietly falls back."""

    def setUp(self) -> None:
        reset_for_tests()
        # Block the import — even if tiktoken is in site-packages,
        # builtins __import__ raising ImportError forces the except branch.
        self._patch = mock.patch.dict(sys.modules, {"tiktoken": None})
        self._patch.start()

    def tearDown(self) -> None:
        self._patch.stop()
        reset_for_tests()

    def test_method_is_heuristic(self) -> None:
        counter = TokenCounter()
        self.assertEqual(counter.get_method(), "heuristic")

    def test_count_uses_heuristic_formula(self) -> None:
        counter = TokenCounter()
        # 10 Chinese chars → 16 tokens by formula.
        self.assertEqual(counter.count("一二三四五六七八九十"), 16)


class TestSingleton(unittest.TestCase):

    def setUp(self) -> None:
        reset_for_tests()

    def tearDown(self) -> None:
        reset_for_tests()

    def test_get_returns_same_instance(self) -> None:
        a = get_token_counter()
        b = get_token_counter()
        self.assertIs(a, b)

    def test_reset_drops_singleton(self) -> None:
        a = get_token_counter()
        reset_for_tests()
        b = get_token_counter()
        self.assertIsNot(a, b)


if __name__ == "__main__":
    unittest.main()
