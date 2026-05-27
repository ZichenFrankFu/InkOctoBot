"""Tests for the LLM fallback router (Phase 1)."""
from __future__ import annotations

import asyncio
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from llm.fallback_router import (
    FallbackRouter, LLMFallbackExhausted, get_fallback_router,
    reset_for_tests,
)


class _StubRouter:
    """Hand-rolled ModelRouter substitute. Configurable per-role
    behavior + resolves bindings without touching real providers."""

    def __init__(self) -> None:
        self.bindings: dict[str, tuple[str, str]] = {}
        self.behavior: dict[str, str] = {}  # role -> "ok" | "fail"
        self.calls: list[str] = []

    def resolve_role(self, role: str) -> tuple[str, str]:
        return self.bindings.get(role, ("", ""))

    async def invoke(self, *, role, prompt, system=None,
                      max_tokens=2000, temperature=0.3, **kw):
        self.calls.append(role)
        beh = self.behavior.get(role, "ok")
        if beh == "fail":
            raise RuntimeError(f"{role} simulated failure")
        return f"{role}-response"


class TestPrimarySucceeds(unittest.IsolatedAsyncioTestCase):

    async def test_returns_primary_response(self) -> None:
        sr = _StubRouter()
        sr.bindings = {"post_commit": ("ollama", "qwen2.5:14b")}
        fr = FallbackRouter(sr)
        out = await fr.invoke(primary_role="post_commit", prompt="hi")
        self.assertEqual(out, "post_commit-response")
        self.assertEqual(sr.calls, ["post_commit"])


class TestFallbackKicksIn(unittest.IsolatedAsyncioTestCase):

    async def test_falls_back_when_primary_fails(self) -> None:
        sr = _StubRouter()
        sr.bindings = {
            "post_commit":          ("ollama", "qwen2.5:14b"),
            "post_commit_fallback": ("anthropic", "claude-haiku-4-5"),
        }
        sr.behavior["post_commit"] = "fail"
        fr = FallbackRouter(sr)
        out = await fr.invoke(primary_role="post_commit", prompt="hi")
        self.assertEqual(out, "post_commit_fallback-response")
        self.assertEqual(sr.calls, ["post_commit", "post_commit_fallback"])


class TestBothFail(unittest.IsolatedAsyncioTestCase):

    async def test_both_fail_raises_exhausted(self) -> None:
        sr = _StubRouter()
        sr.bindings = {
            "post_commit":          ("ollama", "qwen2.5:14b"),
            "post_commit_fallback": ("anthropic", "claude-haiku-4-5"),
        }
        sr.behavior["post_commit"]          = "fail"
        sr.behavior["post_commit_fallback"] = "fail"
        fr = FallbackRouter(sr)
        with self.assertRaises(LLMFallbackExhausted) as cm:
            await fr.invoke(primary_role="post_commit", prompt="hi")
        # Both attempts captured for the notification message.
        self.assertEqual(len(cm.exception.attempts), 2)
        self.assertEqual(cm.exception.attempts[0].role, "post_commit")
        self.assertEqual(cm.exception.attempts[1].role, "post_commit_fallback")
        self.assertIn("simulated failure", cm.exception.attempts[0].error)


class TestNoFallbackBinding(unittest.IsolatedAsyncioTestCase):

    async def test_unbound_fallback_skipped(self) -> None:
        """If the fallback role has no binding, the chain is just the
        primary — a single failure raises with one attempt."""
        sr = _StubRouter()
        sr.bindings = {"post_commit": ("ollama", "qwen2.5:14b")}
        sr.behavior["post_commit"] = "fail"
        fr = FallbackRouter(sr)
        with self.assertRaises(LLMFallbackExhausted) as cm:
            await fr.invoke(primary_role="post_commit", prompt="hi")
        self.assertEqual(len(cm.exception.attempts), 1)

    async def test_fallback_pointing_at_same_binding_skipped(self) -> None:
        """If fallback resolves to the same provider/model as primary,
        retrying it would be pointless — chain length is still 1."""
        sr = _StubRouter()
        sr.bindings = {
            "post_commit":          ("ollama", "qwen2.5:14b"),
            "post_commit_fallback": ("ollama", "qwen2.5:14b"),
        }
        sr.behavior["post_commit"] = "fail"
        fr = FallbackRouter(sr)
        with self.assertRaises(LLMFallbackExhausted) as cm:
            await fr.invoke(primary_role="post_commit", prompt="hi")
        self.assertEqual(len(cm.exception.attempts), 1)


class TestGlobalAccessor(unittest.TestCase):

    def setUp(self) -> None:
        reset_for_tests()

    def tearDown(self) -> None:
        reset_for_tests()

    def test_explicit_router_returns_fresh_wrapper(self) -> None:
        sr1 = _StubRouter()
        sr2 = _StubRouter()
        fr1 = get_fallback_router(sr1)
        fr2 = get_fallback_router(sr2)
        self.assertIsNot(fr1, fr2)


if __name__ == "__main__":
    unittest.main()
