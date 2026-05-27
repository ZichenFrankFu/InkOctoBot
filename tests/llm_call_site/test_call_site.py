"""End-to-end tests for the unified LLMCallSite abstraction."""
from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from framework import global_event_bus
from llm import manual_paste
from llm.call_site import LLMCallSite
from storage.project_schema import ensure_creation_tables


def _fresh_db() -> str:
    db = os.path.join(tempfile.mkdtemp(), "cs.db")
    con = sqlite3.connect(db)
    ensure_creation_tables(con)
    con.commit()
    con.close()
    return db


class TestAutoMode(unittest.IsolatedAsyncioTestCase):

    def setUp(self) -> None:
        global_event_bus.reset_for_tests()
        manual_paste.reset_for_tests()
        self.db = _fresh_db()

    def tearDown(self) -> None:
        global_event_bus.reset_for_tests()
        manual_paste.reset_for_tests()

    async def test_auto_invoke_writes_audit_row(self) -> None:
        cs = LLMCallSite(
            call_site_id="test.foo",
            primary_role="post_commit",
            parsed_target_table="chapter_summaries",
        )
        async def fake_invoke(*, primary_role, fallback_role,
                              prompt, system, max_tokens, temperature):
            return "fake llm response"
        with mock.patch(
            "llm.fallback_router.get_fallback_router",
            return_value=mock.MagicMock(invoke=fake_invoke),
        ):
            raw = await cs.invoke(
                prompt="test prompt", system="test system",
                project_id="p1", chapter_id="ch1",
                parsed_target_row_id="sum_xyz",
                db_path=self.db, manual_mode=False,
            )
        self.assertEqual(raw, "fake llm response")
        # Verify audit row.
        with sqlite3.connect(self.db) as con:
            con.row_factory = sqlite3.Row
            rows = [dict(r) for r in con.execute(
                "SELECT * FROM llm_outputs WHERE call_site_id = 'test.foo'"
            )]
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["source"], "auto")
        self.assertEqual(row["response_raw"], "fake llm response")
        self.assertEqual(row["parsed_target_table"], "chapter_summaries")
        self.assertEqual(row["parsed_target_row_id"], "sum_xyz")
        self.assertEqual(row["project_id"], "p1")
        self.assertEqual(row["chapter_id"], "ch1")
        self.assertIsNotNone(row["prompt_hash"])
        self.assertGreater(row["token_count_prompt"] or 0, 0)


class TestManualMode(unittest.IsolatedAsyncioTestCase):

    def setUp(self) -> None:
        global_event_bus.reset_for_tests()
        manual_paste.reset_for_tests()
        self.db = _fresh_db()

    def tearDown(self) -> None:
        global_event_bus.reset_for_tests()
        manual_paste.reset_for_tests()

    async def test_manual_invoke_awaits_paste(self) -> None:
        cs = LLMCallSite(
            call_site_id="test.bar",
            primary_role="post_commit",
        )

        async def submit_later() -> None:
            # Wait for the register to happen.
            for _ in range(50):
                pending = manual_paste.list_pending()
                if pending:
                    break
                await asyncio.sleep(0.02)
            else:
                self.fail("paste never registered")
            tok = pending[0]["paste_token"]
            manual_paste.submit_paste(tok, "user-typed response")

        asyncio.create_task(submit_later())
        raw = await cs.invoke(
            prompt="some prompt", system="",
            project_id="p1", db_path=self.db, manual_mode=True,
        )
        self.assertEqual(raw, "user-typed response")
        # Audit row with source='manual'.
        with sqlite3.connect(self.db) as con:
            row = con.execute(
                "SELECT source, response_raw FROM llm_outputs "
                "WHERE call_site_id = 'test.bar'"
            ).fetchone()
        self.assertEqual(row[0], "manual")
        self.assertEqual(row[1], "user-typed response")


class TestDryRun(unittest.TestCase):

    def test_dry_run_does_not_call_llm(self) -> None:
        cs = LLMCallSite(call_site_id="test.dry", primary_role="post_commit")
        out = cs.dry_run("prompt text", "system text")
        # No LLM, no DB write — just sizing.
        self.assertEqual(out["call_site_id"], "test.dry")
        self.assertEqual(out["prompt"], "prompt text")
        self.assertGreater(out["prompt_tokens"], 0)
        self.assertEqual(len(out["prompt_hash"]), 16)


class TestManualModeGlobalToggle(unittest.IsolatedAsyncioTestCase):

    def setUp(self) -> None:
        global_event_bus.reset_for_tests()
        manual_paste.reset_for_tests()
        self.db = _fresh_db()

    def tearDown(self) -> None:
        global_event_bus.reset_for_tests()
        manual_paste.reset_for_tests()

    async def test_settings_json_true_triggers_manual(self) -> None:
        """Mock the call_site._is_manual_mode helper directly — pydantic
        Settings can't be monkey-patched, so we exercise the toggle at
        the helper-function boundary."""
        cs = LLMCallSite(call_site_id="test.gtoggle", primary_role="post_commit")

        async def submit_later() -> None:
            for _ in range(50):
                p = manual_paste.list_pending()
                if p:
                    break
                await asyncio.sleep(0.02)
            else:
                self.fail("paste never registered")
            manual_paste.submit_paste(p[0]["paste_token"], "manual response")

        with mock.patch("llm.call_site._is_manual_mode", return_value=True):
            asyncio.create_task(submit_later())
            raw = await cs.invoke(prompt="hi", db_path=self.db)
        self.assertEqual(raw, "manual response")


if __name__ == "__main__":
    unittest.main()
