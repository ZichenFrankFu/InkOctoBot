"""Tests for the read-only learning-view API endpoints powering the
new ``PreferencesPage`` / ``TruthReviewPage`` UI.

These endpoints didn't exist before — the UI couldn't show what Part A
had learned or which chapters had failed audit. The thin layer over
the DB still needs to round-trip cleanly (empty list when no data,
correct filtering, threshold round-trip)."""
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

from storage.project_schema import ensure_creation_tables


def _fresh_db() -> str:
    db = os.path.join(tempfile.mkdtemp(), "lv.db")
    con = sqlite3.connect(db)
    ensure_creation_tables(con)
    con.execute(
        "INSERT INTO projects (project_id, title) VALUES ('p1', 'lv-test')"
    )
    con.execute(
        "INSERT INTO chapters (chapter_id, project_id, chapter_num, "
        "title, audit_status, audit_issues_json) VALUES "
        "('ch1', 'p1', 1, 'C1', 'audit_passed', '[]'),"
        "('ch2', 'p1', 2, 'C2', 'audit_failed', "
        "'[{\"type\":\"consistency\",\"severity\":\"high\","
        "\"description\":\"角色矛盾\"}]')"
    )
    con.execute(
        "INSERT INTO user_style_preferences "
        "(pref_id, project_id, preference_type, description, "
        "confidence, observation_count) VALUES "
        "('p_a', 'p1', 'style', '偏好短句', 0.45, 2),"
        "('p_b', 'p1', 'content', '强调技术细节', 0.3, 1)"
    )
    con.execute(
        "INSERT INTO edit_observations "
        "(obs_id, project_id, chapter_id, chapter_num, "
        "edited_text, diff_stats_json, consumed, created_at) VALUES "
        "('o1', 'p1', 'ch1', 1, 'edited 1', '{}', 1, 100),"
        "('o2', 'p1', 'ch2', 2, 'edited 2', '{}', 0, 200)"
    )
    con.commit()
    con.close()
    return db


def _client(db: str) -> TestClient:
    with mock.patch(
        "ui.backend.app.services.project_paths.get_db_path",
        return_value=db,
    ):
        # Import inside the patch so the router resolves the right DB.
        from ui.backend.app.routers.learning_view_api import router
        app = FastAPI()
        app.include_router(router)
        return TestClient(app)


class TestPreferencesEndpoints(unittest.TestCase):

    def setUp(self) -> None:
        self.db = _fresh_db()
        self.patcher = mock.patch(
            "ui.backend.app.routers.learning_view_api.get_db_path",
            return_value=self.db,
        )
        self.patcher.start()
        from ui.backend.app.routers.learning_view_api import router
        app = FastAPI()
        app.include_router(router)
        self.client = TestClient(app)

    def tearDown(self) -> None:
        self.patcher.stop()

    def test_list_preferences_returns_seeded_rows(self) -> None:
        r = self.client.get("/api/learning/preferences?project_id=p1")
        self.assertEqual(r.status_code, 200)
        items = r.json()["items"]
        self.assertEqual(len(items), 2)
        # Ordered by confidence desc.
        self.assertEqual(items[0]["description"], "偏好短句")

    def test_min_confidence_filters(self) -> None:
        r = self.client.get(
            "/api/learning/preferences?project_id=p1&min_confidence=0.4"
        )
        items = r.json()["items"]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["pref_id"], "p_a")

    def test_delete_preference(self) -> None:
        r = self.client.delete("/api/learning/preferences/p_a")
        self.assertEqual(r.status_code, 200)
        r = self.client.get("/api/learning/preferences?project_id=p1")
        self.assertEqual(len(r.json()["items"]), 1)

    def test_observations_filter_by_consumed(self) -> None:
        all_ = self.client.get(
            "/api/learning/observations?project_id=p1"
        ).json()["items"]
        unconsumed = self.client.get(
            "/api/learning/observations?project_id=p1&consumed=0"
        ).json()["items"]
        self.assertEqual(len(all_), 2)
        self.assertEqual(len(unconsumed), 1)
        self.assertEqual(unconsumed[0]["obs_id"], "o2")

    def test_threshold_status(self) -> None:
        r = self.client.get("/api/learning/threshold-status?project_id=p1")
        d = r.json()
        self.assertIn("threshold", d)
        self.assertIn("unconsumed", d)
        self.assertEqual(d["unconsumed"], 1)

    def test_threshold_round_trip(self) -> None:
        r = self.client.post("/api/learning/threshold", json={"threshold": 7})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["threshold"], 7)
        d = self.client.get("/api/learning/threshold-status?project_id=p1").json()
        self.assertEqual(d["threshold"], 7)


class TestChapterAuditEndpoint(unittest.TestCase):

    def setUp(self) -> None:
        self.db = _fresh_db()
        self.patcher = mock.patch(
            "ui.backend.app.routers.learning_view_api.get_db_path",
            return_value=self.db,
        )
        self.patcher.start()
        from ui.backend.app.routers.learning_view_api import router
        app = FastAPI()
        app.include_router(router)
        self.client = TestClient(app)

    def tearDown(self) -> None:
        self.patcher.stop()

    def test_list_chapter_audits_parses_issues(self) -> None:
        r = self.client.get("/api/learning/chapter-audits?project_id=p1")
        self.assertEqual(r.status_code, 200)
        items = r.json()["items"]
        self.assertEqual(len(items), 2)
        failed = [it for it in items if it["audit_status"] == "audit_failed"][0]
        self.assertEqual(len(failed["audit_issues"]), 1)
        self.assertEqual(failed["audit_issues"][0]["severity"], "high")

    def test_override_audit(self) -> None:
        r = self.client.post(
            "/api/learning/override-audit",
            json={"chapter_id": "ch2"},
        )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["status"], "audit_overridden")
        items = self.client.get(
            "/api/learning/chapter-audits?project_id=p1"
        ).json()["items"]
        ch2 = [it for it in items if it["chapter_id"] == "ch2"][0]
        self.assertEqual(ch2["audit_status"], "audit_overridden")


if __name__ == "__main__":
    unittest.main()
