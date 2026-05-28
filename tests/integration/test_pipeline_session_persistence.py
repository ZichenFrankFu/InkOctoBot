"""Stage 5: ``pipeline_sessions`` table + DB-mirroring store.

The in-memory ``_active_sessions`` dict is still authoritative at
runtime — it owns the asyncio.Task / Event handles. After every state
transition we mirror the persistable subset to the
``pipeline_sessions`` table so:

  1. A server restart can still surface what was happening (status,
     events, partial result).
  2. Sessions whose process was killed get re-tagged as
     ``'interrupted'`` on the next boot (their Task is unrecoverable).
  3. The History view has a real source instead of a flaky cache.

These tests cover:
  * Table DDL is idempotent + included by ``ensure_creation_tables``.
  * ``upsert_session`` round-trips the persistable fields.
  * Runtime handles (Task / Event) are dropped, not crashed on.
  * ``mark_running_as_interrupted`` flips the right rows on boot.
  * /api/generation/sessions endpoint surfaces what the store stores.
  * /api/generation/status/{id} falls back to DB when the in-memory
    session has been evicted.
"""
from __future__ import annotations

import asyncio
import os
import sqlite3
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from fastapi.testclient import TestClient

from storage.pipeline_session_schema import ensure_pipeline_session_table
from storage.project_schema import ensure_creation_tables


def _fresh_db() -> str:
    db = os.path.join(tempfile.mkdtemp(), "ps.db")
    con = sqlite3.connect(db)
    ensure_creation_tables(con)
    con.execute(
        "INSERT INTO projects (project_id, title) VALUES ('p1', 'session-stage5')"
    )
    con.execute(
        "INSERT INTO chapters (chapter_id, project_id, chapter_num) "
        "VALUES ('ch1', 'p1', 1)"
    )
    con.commit()
    con.close()
    return db


# ─────────── Schema ───────────


class TestPipelineSessionSchema(unittest.TestCase):

    def test_table_created_by_creation_tables(self) -> None:
        db = _fresh_db()
        with sqlite3.connect(db) as c:
            row = c.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name='pipeline_sessions'",
            ).fetchone()
        self.assertIsNotNone(row, "ensure_creation_tables should bootstrap "
                              "pipeline_sessions")

    def test_ensure_is_idempotent(self) -> None:
        db = _fresh_db()
        with sqlite3.connect(db) as c:
            ensure_pipeline_session_table(c)
            ensure_pipeline_session_table(c)
        # No error on second call.

    def test_indexes_exist(self) -> None:
        db = _fresh_db()
        with sqlite3.connect(db) as c:
            idx_names = {
                r[0] for r in c.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE type='index' AND tbl_name='pipeline_sessions'",
                ).fetchall()
            }
        self.assertIn("idx_pipeline_sessions_status", idx_names)
        self.assertIn("idx_pipeline_sessions_project", idx_names)
        self.assertIn("idx_pipeline_sessions_chapter", idx_names)

    def test_status_check_constraint(self) -> None:
        """CHECK on status prevents bogus values from being persisted."""
        db = _fresh_db()
        with sqlite3.connect(db) as c:
            with self.assertRaises(sqlite3.IntegrityError):
                c.execute(
                    "INSERT INTO pipeline_sessions "
                    "(session_id, status, created_at, updated_at) "
                    "VALUES ('s1', 'BOGUS', 1.0, 1.0)",
                )


# ─────────── Store helpers ───────────


class TestPipelineSessionStore(unittest.TestCase):

    def setUp(self) -> None:
        self.db = _fresh_db()

    def test_upsert_inserts_then_updates(self) -> None:
        from ui.backend.app.services import pipeline_session_store
        session = {
            "status": "running", "request": {"project_id": "p1", "x": 1},
            "events": [{"type": "start", "ts": 1.0}],
            "result": None, "text": "",
            "current_step": "scene_director",
            "waiting_confirm": False, "waiting_manual": False,
            "trace_id": "trc-1", "created_at": 100.0,
        }
        pipeline_session_store.upsert_session(
            self.db, "s1", session,
            project_id="p1", chapter_id="ch1", chapter_num=1,
        )
        row = pipeline_session_store.get_session(self.db, "s1")
        self.assertIsNotNone(row)
        self.assertEqual(row["status"], "running")
        self.assertEqual(row["current_step"], "scene_director")
        self.assertEqual(row["request"]["x"], 1)
        self.assertEqual(len(row["events"]), 1)

        # Update path
        session["status"] = "complete"
        session["current_step"] = "evaluator"
        session["text"] = "第1章完成"
        session["events"].append({"type": "complete", "ts": 2.0})
        pipeline_session_store.upsert_session(
            self.db, "s1", session,
            project_id="p1", chapter_id="ch1", chapter_num=1,
        )
        row2 = pipeline_session_store.get_session(self.db, "s1")
        self.assertEqual(row2["status"], "complete")
        self.assertEqual(row2["text"], "第1章完成")
        self.assertEqual(len(row2["events"]), 2)
        self.assertIsNotNone(row2["completed_at"])

    def test_upsert_drops_unpersistable_runtime_handles(self) -> None:
        """asyncio.Task / Event / ChapterContext objects must not crash
        the JSON encode — the helper silently drops them via
        ``default=str``."""
        from ui.backend.app.services import pipeline_session_store

        # Simulate a running session with non-serializable members.
        session = {
            "status": "running",
            "request": {"project_id": "p1"},
            "events": [],
            "result": None, "text": "",
            "current_step": "",
            "waiting_confirm": False, "waiting_manual": False,
            "trace_id": "",
            "created_at": time.time(),
            # These would normally blow up json.dumps without default=str:
            "task": object(),
            "confirm_event": object(),
            "manual_event": object(),
        }
        # Must NOT raise.
        pipeline_session_store.upsert_session(
            self.db, "s-handles", session, project_id="p1",
        )
        row = pipeline_session_store.get_session(self.db, "s-handles")
        self.assertIsNotNone(row)
        self.assertEqual(row["status"], "running")

    def test_upsert_swallows_db_errors(self) -> None:
        """A bad db_path doesn't propagate — best-effort persistence."""
        from ui.backend.app.services import pipeline_session_store
        # Should NOT raise even with a totally invalid path.
        pipeline_session_store.upsert_session(
            "/nonexistent/dir/missing.db", "s-bad", {"status": "running"},
        )

    def test_mark_running_as_interrupted_flips_only_active(self) -> None:
        from ui.backend.app.services import pipeline_session_store
        # Seed: one running, one paused, one complete, one error.
        for sid, status in [
            ("a", "running"), ("b", "paused_audit_review"),
            ("c", "complete"), ("d", "error"),
        ]:
            pipeline_session_store.upsert_session(
                self.db, sid,
                {"status": status, "created_at": time.time(),
                 "request": {}, "events": []},
                project_id="p1",
            )
        n = pipeline_session_store.mark_running_as_interrupted(self.db)
        self.assertEqual(n, 2)
        # Verify final state
        with sqlite3.connect(self.db) as c:
            statuses = {
                r[0]: r[1] for r in c.execute(
                    "SELECT session_id, status FROM pipeline_sessions",
                ).fetchall()
            }
        self.assertEqual(statuses["a"], "interrupted")
        self.assertEqual(statuses["b"], "interrupted")
        self.assertEqual(statuses["c"], "complete")
        self.assertEqual(statuses["d"], "error")

    def test_list_sessions_filters(self) -> None:
        from ui.backend.app.services import pipeline_session_store
        # Two projects, three statuses.
        seeds = [
            ("p1-a", "p1", "complete"), ("p1-b", "p1", "error"),
            ("p1-c", "p1", "complete"),
            ("p2-a", "p2", "complete"),
        ]
        base = time.time()
        for i, (sid, pid, status) in enumerate(seeds):
            pipeline_session_store.upsert_session(
                self.db, sid, {
                    "status": status, "created_at": base + i,
                    "request": {}, "events": [],
                }, project_id=pid,
            )
        # By project
        p1 = pipeline_session_store.list_sessions(self.db, project_id="p1")
        self.assertEqual(len(p1), 3)
        # By status (single)
        ok = pipeline_session_store.list_sessions(
            self.db, status_filter="complete",
        )
        self.assertEqual(len(ok), 3)
        # By status (multi)
        either = pipeline_session_store.list_sessions(
            self.db, status_filter=["error", "complete"],
        )
        self.assertEqual(len(either), 4)
        # By project + status combined
        p1_ok = pipeline_session_store.list_sessions(
            self.db, project_id="p1", status_filter="complete",
        )
        self.assertEqual(len(p1_ok), 2)


# ─────────── /api/generation HTTP integration ───────────


class TestGenerationSessionEndpoints(unittest.TestCase):

    def setUp(self) -> None:
        self.db = _fresh_db()
        self._patches = []
        for p in (
            mock.patch(
                "ui.backend.app.services.project_paths.get_db_path",
                return_value=self.db,
            ),
            mock.patch(
                "ui.backend.app.routers.generation_api._get_db_path",
                return_value=self.db,
            ),
            mock.patch(
                "ui.backend.app.routers.generation_api._run_pipeline_background",
                return_value=None,
            ),
            mock.patch("asyncio.create_task", return_value=None),
        ):
            p.start()
            self._patches.append(p)

    def tearDown(self) -> None:
        # Clean up any leftover _active_sessions to keep tests isolated.
        from ui.backend.app.routers.generation_api import _active_sessions
        _active_sessions.clear()
        for p in self._patches:
            p.stop()

    def _client(self) -> TestClient:
        from fastapi import FastAPI
        from ui.backend.app.routers.generation_api import router
        app = FastAPI()
        app.include_router(router)
        return TestClient(app)

    def test_start_creates_db_row(self) -> None:
        """POST /api/generation/start should insert a pipeline_sessions
        row before returning, so a crash mid-pipeline still leaves
        diagnostics behind."""
        client = self._client()
        res = client.post("/api/generation/start", json={
            "project_id": "p1", "chapter_id": "ch1", "chapter_num": 1,
            "synopsis": "测试", "characters": ["主角"],
        })
        self.assertEqual(res.status_code, 200, res.text)
        sid = res.json()["session_id"]
        with sqlite3.connect(self.db) as c:
            row = c.execute(
                "SELECT session_id, project_id, chapter_id, chapter_num, status "
                "FROM pipeline_sessions WHERE session_id = ?", (sid,),
            ).fetchone()
        self.assertIsNotNone(row, "session row missing from pipeline_sessions")
        self.assertEqual(row[1], "p1")
        self.assertEqual(row[2], "ch1")
        self.assertEqual(row[3], 1)
        self.assertEqual(row[4], "running")

    def test_sessions_list_endpoint(self) -> None:
        client = self._client()
        for ch in ("ch1", "ch1"):
            res = client.post("/api/generation/start", json={
                "project_id": "p1", "chapter_id": ch, "chapter_num": 1,
                "synopsis": "x",
            })
            self.assertEqual(res.status_code, 200)
        listing = client.get("/api/generation/sessions?project_id=p1")
        self.assertEqual(listing.status_code, 200)
        data = listing.json()
        self.assertGreaterEqual(data["count"], 2)
        self.assertEqual(len(data["sessions"]), data["count"])
        # Each row has the canonical shape.
        for row in data["sessions"]:
            self.assertIn("status", row)
            self.assertIn("session_id", row)
            self.assertIn("events", row)  # decoded back from JSON

    def test_status_falls_back_to_db_after_eviction(self) -> None:
        """When in-memory _active_sessions no longer has the id, GET
        /status/{sid} should still find it in the DB and return
        ``persisted: true``."""
        from ui.backend.app.routers.generation_api import _active_sessions
        client = self._client()
        res = client.post("/api/generation/start", json={
            "project_id": "p1", "chapter_id": "ch1", "chapter_num": 1,
            "synopsis": "x",
        })
        sid = res.json()["session_id"]
        # Evict from memory.
        _active_sessions.pop(sid, None)
        # GET /status now reads from DB.
        st = client.get(f"/api/generation/status/{sid}")
        self.assertEqual(st.status_code, 200, st.text)
        body = st.json()
        self.assertEqual(body["status"], "running")
        self.assertTrue(body["persisted"])

    def test_status_404_when_neither_memory_nor_db(self) -> None:
        client = self._client()
        st = client.get("/api/generation/status/nonexistent-id")
        self.assertEqual(st.status_code, 404)


if __name__ == "__main__":
    unittest.main()
