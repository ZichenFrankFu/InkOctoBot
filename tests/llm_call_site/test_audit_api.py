"""Tests for the unified audit + paste-inbox HTTP API."""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from fastapi import FastAPI
from fastapi.testclient import TestClient

from framework import global_event_bus
from llm import manual_paste
from storage.project_schema import ensure_creation_tables


def _fresh_db() -> str:
    db = os.path.join(tempfile.mkdtemp(), "api.db")
    con = sqlite3.connect(db)
    ensure_creation_tables(con)
    con.commit()
    con.close()
    return db


def _seed_audit_rows(db: str) -> None:
    with sqlite3.connect(db) as con:
        for i, source in enumerate(("auto", "auto", "manual", "auto")):
            con.execute(
                "INSERT INTO llm_outputs (output_id, call_site_id, "
                "project_id, prompt_hash, prompt_full, response_raw, source) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (f"lo_{i:04d}",
                 "test.alpha" if i < 2 else "test.beta",
                 "p1" if i % 2 == 0 else "p2",
                 f"h{i:016d}"[:16], "prompt text",
                 f"response {i}", source),
            )
        con.commit()


class TestAuditAPI(unittest.TestCase):

    def setUp(self) -> None:
        self.db = _fresh_db()
        _seed_audit_rows(self.db)
        from ui.backend.app.routers.llm_audit_api import router
        app = FastAPI()
        app.include_router(router)
        self._patch = mock.patch(
            "ui.backend.app.routers.llm_audit_api.get_db_path",
            return_value=self.db,
        )
        self._patch.start()
        self.client = TestClient(app)

    def tearDown(self) -> None:
        self._patch.stop()

    def test_list_all(self) -> None:
        res = self.client.get("/api/llm-audit")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["count"], 4)

    def test_filter_by_call_site(self) -> None:
        res = self.client.get("/api/llm-audit?call_site_id=test.alpha")
        self.assertEqual(res.json()["count"], 2)

    def test_filter_by_source(self) -> None:
        res = self.client.get("/api/llm-audit?source=manual")
        self.assertEqual(res.json()["count"], 1)
        self.assertEqual(res.json()["outputs"][0]["output_id"], "lo_0002")

    def test_filter_bad_source(self) -> None:
        res = self.client.get("/api/llm-audit?source=invalid")
        self.assertEqual(res.status_code, 400)

    def test_filter_project(self) -> None:
        res = self.client.get("/api/llm-audit?project_id=p1")
        # p1 has rows 0 and 2.
        self.assertEqual(res.json()["count"], 2)

    def test_get_one(self) -> None:
        res = self.client.get("/api/llm-audit/lo_0001")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["call_site_id"], "test.alpha")

    def test_get_unknown(self) -> None:
        self.assertEqual(self.client.get("/api/llm-audit/lo_nope").status_code, 404)

    def test_acknowledge(self) -> None:
        self.assertEqual(self.client.post(
            "/api/llm-audit/lo_0000/acknowledge").status_code, 200)
        # Unacknowledged_only should now exclude it.
        unacked = self.client.get("/api/llm-audit?unacknowledged_only=true")
        ids = {o["output_id"] for o in unacked.json()["outputs"]}
        self.assertNotIn("lo_0000", ids)

    def test_mark_corrected_implies_acknowledged(self) -> None:
        res = self.client.post("/api/llm-audit/lo_0001/mark-corrected")
        self.assertEqual(res.status_code, 200)
        row = self.client.get("/api/llm-audit/lo_0001").json()
        self.assertEqual(row["user_corrected"], 1)
        self.assertEqual(row["user_acknowledged"], 1)

    def test_stats(self) -> None:
        res = self.client.get("/api/llm-audit/stats")
        body = res.json()
        self.assertEqual(body["by_source"]["auto"], 3)
        self.assertEqual(body["by_source"]["manual"], 1)
        self.assertEqual(body["by_call_site"]["test.alpha"], 2)
        self.assertEqual(body["unacknowledged"], 4)


class TestPasteAPI(unittest.IsolatedAsyncioTestCase):

    def setUp(self) -> None:
        global_event_bus.reset_for_tests()
        manual_paste.reset_for_tests()
        from ui.backend.app.routers.llm_paste_api import router
        app = FastAPI()
        app.include_router(router)
        self.client = TestClient(app)

    def tearDown(self) -> None:
        global_event_bus.reset_for_tests()
        manual_paste.reset_for_tests()

    async def test_list_empty(self) -> None:
        res = self.client.get("/api/llm-paste/pending")
        self.assertEqual(res.json(), {"pending": []})

    async def test_full_lifecycle(self) -> None:
        # Register a pending paste from outside the HTTP layer.
        import asyncio
        loop = asyncio.get_event_loop()
        token = manual_paste.register_pending_paste(
            "test.api", "the prompt", "the system",
            project_id="p1", chapter_id="ch1", loop=loop,
        )

        # GET the prompt to copy.
        res = self.client.get(f"/api/llm-paste/{token}")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["prompt"], "the prompt")
        self.assertEqual(res.json()["call_site_id"], "test.api")

        # List shows it.
        listing = self.client.get("/api/llm-paste/pending").json()
        self.assertEqual(len(listing["pending"]), 1)

        # Submit response.
        res = self.client.post(
            f"/api/llm-paste/{token}",
            json={"response_raw": "user response"},
        )
        self.assertEqual(res.status_code, 200)

        # Future is resolved; cleanup happens when await_paste returns,
        # but the future itself should be marked done already (or about
        # to be — the call_soon_threadsafe might still be queued).
        pp = manual_paste.get_pending(token)
        if pp is not None:  # still in queue, future may or may not be done
            # Wait a tick for call_soon_threadsafe to fire.
            await asyncio.sleep(0.05)
        # Re-submitting after the first submit should now 404 (future
        # done or token consumed).
        retry = self.client.post(
            f"/api/llm-paste/{token}",
            json={"response_raw": "second submit"},
        )
        self.assertEqual(retry.status_code, 404)

    async def test_submit_unknown_token(self) -> None:
        res = self.client.post(
            "/api/llm-paste/pst_doesnotexist",
            json={"response_raw": "x"},
        )
        self.assertEqual(res.status_code, 404)

    async def test_submit_missing_body(self) -> None:
        res = self.client.post("/api/llm-paste/pst_x", json={})
        self.assertEqual(res.status_code, 400)


if __name__ == "__main__":
    unittest.main()
