"""Tests for the manual paste pipeline."""
from __future__ import annotations

import asyncio
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from framework import global_event_bus
from llm import manual_paste


class TestPasteLifecycle(unittest.IsolatedAsyncioTestCase):

    def setUp(self) -> None:
        global_event_bus.reset_for_tests()
        manual_paste.reset_for_tests()

    def tearDown(self) -> None:
        global_event_bus.reset_for_tests()
        manual_paste.reset_for_tests()

    async def test_register_then_submit_resolves_future(self) -> None:
        token = manual_paste.register_pending_paste(
            "test_site", "the prompt", "the system",
        )
        # Token shape contract.
        self.assertTrue(token.startswith("pst_"))
        # In a background task, submit the response after a tick.
        async def submit_later():
            await asyncio.sleep(0.01)
            manual_paste.submit_paste(token, "user-pasted response")
        asyncio.create_task(submit_later())
        result = await manual_paste.await_paste(token, timeout=2)
        self.assertEqual(result, "user-pasted response")

    async def test_timeout_raises(self) -> None:
        token = manual_paste.register_pending_paste("test_site", "prompt")
        with self.assertRaises(manual_paste.LLMPasteTimeout):
            await manual_paste.await_paste(token, timeout=0.1)

    async def test_cancel_raises(self) -> None:
        token = manual_paste.register_pending_paste("test_site", "prompt")
        async def cancel_later():
            await asyncio.sleep(0.01)
            manual_paste.cancel_paste(token)
        asyncio.create_task(cancel_later())
        with self.assertRaises(manual_paste.LLMPasteCancelled):
            await manual_paste.await_paste(token, timeout=2)

    async def test_event_bus_push(self) -> None:
        bus = global_event_bus.get_event_bus()
        token = manual_paste.register_pending_paste(
            "post_commit.summarizer", "test prompt", "",
            project_id="p1", chapter_id="ch1",
        )
        suggestions = bus.get_suggestions_nowait()
        self.assertEqual(len(suggestions), 1)
        s = suggestions[0]
        self.assertEqual(s.event_type, "llm_paste_pending")
        self.assertEqual(s.data["paste_token"], token)
        self.assertEqual(s.data["call_site_id"], "post_commit.summarizer")
        manual_paste.cancel_paste(token)


class TestPasteListAndGet(unittest.TestCase):

    def setUp(self) -> None:
        global_event_bus.reset_for_tests()
        manual_paste.reset_for_tests()

    def tearDown(self) -> None:
        global_event_bus.reset_for_tests()
        manual_paste.reset_for_tests()

    def test_list_pending(self) -> None:
        loop = asyncio.new_event_loop()
        try:
            asyncio.set_event_loop(loop)
            t1 = manual_paste.register_pending_paste("site_a", "p1", loop=loop)
            t2 = manual_paste.register_pending_paste("site_b", "p2", loop=loop)
            items = manual_paste.list_pending()
            tokens = {it["paste_token"] for it in items}
            self.assertIn(t1, tokens)
            self.assertIn(t2, tokens)
            # Drain so teardown isn't noisy.
            manual_paste.cancel_paste(t1)
            manual_paste.cancel_paste(t2)
        finally:
            loop.close()

    def test_get_pending_unknown_returns_none(self) -> None:
        self.assertIsNone(manual_paste.get_pending("pst_nonexistent"))


if __name__ == "__main__":
    unittest.main()
