"""Tests for ui/backend/app/routers/snapshot_api.py."""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from storage.project_schema import ensure_creation_tables
from ui.backend.app.services import project_store, snapshot_store


def _fresh_db_with_char() -> tuple[str, str]:
    db = os.path.join(tempfile.mkdtemp(), "api.db")
    con = sqlite3.connect(db)
    con.execute("PRAGMA foreign_keys=ON")
    ensure_creation_tables(con)
    con.execute("INSERT INTO projects (project_id, title) VALUES ('p1','t')")
    con.commit()
    con.close()
    char = project_store.upsert_character(
        db, {"project_id": "p1", "name": "李星河"},
    )
    return db, char["id"]


class TestSnapshotAPI(unittest.TestCase):

    def setUp(self) -> None:
        self.db, self.cid = _fresh_db_with_char()
        self._patcher = mock.patch(
            "ui.backend.app.routers.snapshot_api._db", return_value=self.db,
        )
        self._patcher.start()
        from ui.backend.app.routers.snapshot_api import (
            router, reminder_router,
        )
        app = FastAPI()
        app.include_router(router)
        app.include_router(reminder_router)
        self.client = TestClient(app)

    def tearDown(self) -> None:
        self._patcher.stop()

    def test_create_list_get_update_delete(self) -> None:
        res = self.client.post("/api/snapshots", json={
            "character_id": self.cid, "project_id": "p1",
            "trigger_description": "X",
        })
        self.assertEqual(res.status_code, 200)
        sid = res.json()["snapshot_id"]

        rows = self.client.get(
            "/api/snapshots", params={"character_id": self.cid},
        ).json()["items"]
        self.assertEqual(len(rows), 1)

        got = self.client.get(f"/api/snapshots/{sid}")
        self.assertEqual(got.status_code, 200)

        upd = self.client.put(f"/api/snapshots/{sid}", json={
            "character_id": self.cid,
            "personality_override": "改了",
        })
        self.assertEqual(upd.json()["personality_override"], "改了")

        d = self.client.delete(f"/api/snapshots/{sid}")
        self.assertEqual(d.status_code, 200)
        self.assertEqual(self.client.get(f"/api/snapshots/{sid}").status_code, 404)

    def test_create_validation_400(self) -> None:
        res = self.client.post("/api/snapshots", json={
            "project_id": "p1",   # missing character_id
        })
        self.assertEqual(res.status_code, 400)

    def test_bind_and_mark_complete(self) -> None:
        snap = snapshot_store.upsert_snapshot(self.db, {
            "character_id": self.cid, "project_id": "p1",
        })
        sid = snap["snapshot_id"]

        b = self.client.post(
            f"/api/snapshots/{sid}/bind-chapter", json={"chapter_num": 7},
        )
        self.assertEqual(b.status_code, 200)
        self.assertIn(7, b.json()["bound_chapters"])

        m = self.client.post(
            f"/api/snapshots/{sid}/mark-complete", json={"chapter_num": 9},
        )
        self.assertEqual(m.json()["transition_complete_chapter"], 9)

        u = self.client.post(f"/api/snapshots/{sid}/unmark-complete")
        self.assertIsNone(u.json()["transition_complete_chapter"])

        ub = self.client.post(
            f"/api/snapshots/{sid}/unbind-chapter", json={"chapter_num": 7},
        )
        self.assertNotIn(7, ub.json()["bound_chapters"])

    def test_bind_chapter_missing_field_400(self) -> None:
        snap = snapshot_store.upsert_snapshot(self.db, {
            "character_id": self.cid, "project_id": "p1",
        })
        res = self.client.post(
            f"/api/snapshots/{snap['snapshot_id']}/bind-chapter", json={},
        )
        self.assertEqual(res.status_code, 400)

    def test_404_for_unknown_snapshot(self) -> None:
        self.assertEqual(
            self.client.get("/api/snapshots/nope").status_code, 404,
        )


class TestSnapshotReminderAPI(unittest.TestCase):

    def setUp(self) -> None:
        self.db, self.cid = _fresh_db_with_char()
        self._patcher = mock.patch(
            "ui.backend.app.routers.snapshot_api._db", return_value=self.db,
        )
        self._patcher.start()
        from ui.backend.app.routers.snapshot_api import (
            router, reminder_router,
        )
        app = FastAPI()
        app.include_router(router)
        app.include_router(reminder_router)
        self.client = TestClient(app)
        # Seed an overdue snapshot.
        snapshot_store.upsert_snapshot(self.db, {
            "character_id": self.cid, "project_id": "p1",
            "expected_chapter_range_start": 5,
            "expected_chapter_range_end": 10,
        })

    def tearDown(self) -> None:
        self._patcher.stop()

    def test_list_with_chapter_runs_scanner_and_returns(self) -> None:
        res = self.client.get(
            "/api/snapshot-reminders",
            params={"project_id": "p1", "chapter_num": 12},
        )
        items = res.json()["items"]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["type"], "snapshot_not_started")

    def test_acknowledge_round_trip(self) -> None:
        self.client.get(
            "/api/snapshot-reminders",
            params={"project_id": "p1", "chapter_num": 12},
        )
        items = self.client.get(
            "/api/snapshot-reminders", params={"project_id": "p1"},
        ).json()["items"]
        rid = items[0]["reminder_id"]
        ack = self.client.post(
            f"/api/snapshot-reminders/{rid}/acknowledge",
            json={"action": "confirmed"},
        )
        self.assertEqual(ack.json()["user_action"], "confirmed")

    def test_acknowledge_bad_action_400(self) -> None:
        self.client.get(
            "/api/snapshot-reminders",
            params={"project_id": "p1", "chapter_num": 12},
        )
        rid = self.client.get(
            "/api/snapshot-reminders", params={"project_id": "p1"},
        ).json()["items"][0]["reminder_id"]
        res = self.client.post(
            f"/api/snapshot-reminders/{rid}/acknowledge",
            json={"action": "bogus"},
        )
        self.assertEqual(res.status_code, 400)


if __name__ == "__main__":
    unittest.main()
