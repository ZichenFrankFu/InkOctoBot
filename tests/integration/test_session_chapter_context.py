"""Stage 3 Task 3.4: ``/api/generation/start`` exposes a ChapterContext
object on the session dict so multi-agent agents can pull structured
loader_blocks / truth_bundle instead of grep-ing
``req_data['world_rules']``.

This is opt-in for agents — current Writer / SceneDirector etc. still
use the world_rules string; new agents (or refactored ones) should
prefer ``session['chapter_context']`` for typed access.
"""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from fastapi.testclient import TestClient

from storage.project_schema import ensure_creation_tables


def _fresh_db() -> str:
    db = os.path.join(tempfile.mkdtemp(), "sc.db")
    con = sqlite3.connect(db)
    ensure_creation_tables(con)
    con.execute(
        "INSERT INTO projects (project_id, title) VALUES ('p1', 'session-cc')"
    )
    con.execute(
        "INSERT INTO chapters (chapter_id, project_id, chapter_num) "
        "VALUES ('ch9', 'p1', 9)"
    )
    con.commit()
    con.close()
    return db


class TestSessionChapterContext(unittest.TestCase):

    def setUp(self) -> None:
        self.db = _fresh_db()
        self._patches = []

    def tearDown(self) -> None:
        for p in self._patches:
            p.stop()

    def test_start_attaches_chapter_context_to_session(self) -> None:
        """POST /api/generation/start populates
        _active_sessions[session_id]['chapter_context'] with a
        ChapterContext instance whose loader_blocks + truth_bundle
        match what ChapterContext.build would produce standalone."""
        from ui.backend.app.routers.generation_api import _active_sessions
        from ui.backend.app.services.chapter_context import ChapterContext

        for p in (
            mock.patch(
                "ui.backend.app.services.project_paths.get_db_path",
                return_value=self.db,
            ),
            mock.patch(
                "ui.backend.app.routers.generation_api._get_db_path",
                return_value=self.db,
            ),
            # Block the pipeline background task from actually running
            # so the session sits in 'running' state with the
            # chapter_context attached but no LLM calls happen.
            mock.patch(
                "ui.backend.app.routers.generation_api._run_pipeline_background",
                return_value=None,
            ),
            mock.patch(
                "asyncio.create_task", return_value=None,
            ),
        ):
            p.start()
            self._patches.append(p)

        from fastapi import FastAPI
        from ui.backend.app.routers.generation_api import router
        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)

        res = client.post("/api/generation/start", json={
            "project_id": "p1",
            "chapter_id": "ch9",
            "chapter_num": 9,
            "synopsis": "测试章节",
            "characters": ["主角"],
        })
        self.assertEqual(res.status_code, 200, res.text)
        session_id = res.json()["session_id"]

        session = _active_sessions.get(session_id)
        self.assertIsNotNone(session)
        ctx = session.get("chapter_context")
        self.assertIsInstance(ctx, ChapterContext)
        self.assertEqual(ctx.project_id, "p1")
        self.assertEqual(ctx.chapter_id, "ch9")
        self.assertEqual(ctx.chapter_num, 9)
        # loader_blocks dict shape — keys exist even on empty project.
        self.assertGreater(len(ctx.loader_blocks), 0)

    def test_start_tolerates_chapter_context_build_failure(self) -> None:
        """If ChapterContext.build raises (e.g. broken loader), /start
        still completes — chapter_context is just None on the session."""
        from ui.backend.app.services.chapter_context import ChapterContext

        async def _broken_build(req):
            raise RuntimeError("simulated loader bomb")

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
                "asyncio.create_task", return_value=None,
            ),
            mock.patch.object(ChapterContext, "build", _broken_build),
        ):
            p.start()
            self._patches.append(p)

        from fastapi import FastAPI
        from ui.backend.app.routers.generation_api import router, _active_sessions
        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)

        res = client.post("/api/generation/start", json={
            "project_id": "p1", "chapter_id": "ch9", "chapter_num": 9,
            "synopsis": "测试",
        })
        self.assertEqual(res.status_code, 200, res.text)
        session = _active_sessions.get(res.json()["session_id"])
        self.assertIsNone(session.get("chapter_context"))


if __name__ == "__main__":
    unittest.main()
