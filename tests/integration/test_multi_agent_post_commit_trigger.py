"""Integration test: Multi-agent /start finalize triggers post-commit pipeline.

The previous gap (Stage 1 Task 1 of roadmap 2026 Q2): multi-agent
``_run_pipeline_background`` set ``session['status']='complete'`` and
emitted a ``complete`` event but never wrote ``text_versions`` and never
fired the post-commit pipeline. That meant the 6 sub-tasks (summarizer
/ event_extractor / chromadb_indexer / state_extractor /
snapshot_detector / skill_emitter) silently never ran after a
multi-agent generation — only the manual ``/api/editor/save-version``
path ever triggered them.

This test calls the new ``_persist_and_trigger_post_commit`` helper
directly (the body of which is exercised at end of
``_run_pipeline_inner``) and verifies:

  1. A text_versions row gets inserted with source='ai_generated'.
  2. A commit_tasks row is created per enabled sub-task.
  3. Tasks reach a terminal state (with conftest's LLM stubs they
     should mostly complete; chromadb_indexer fails due to missing
     sentence-transformers in the sandbox, which is the expected
     environmental behavior).
"""
from __future__ import annotations

import asyncio
import os
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from storage.project_schema import ensure_creation_tables


def _fresh_db() -> str:
    db = os.path.join(tempfile.mkdtemp(), "mapc.db")
    con = sqlite3.connect(db)
    ensure_creation_tables(con)
    con.execute(
        "INSERT INTO projects (project_id, title) VALUES ('p1', 'multi-agent test')"
    )
    # text_versions has a FK on chapter_id → chapters; seed chapters
    # rows so insert_version doesn't trip the constraint.
    for cid, cnum in (("ch1", 5), ("ch2", 2)):
        con.execute(
            "INSERT INTO chapters (chapter_id, project_id, chapter_num) "
            "VALUES (?, 'p1', ?)",
            (cid, cnum),
        )
    con.commit()
    con.close()
    return db


class _StubSummary:
    async def __call__(self, *args, **kwargs):
        return ("摘要标题", "摘要正文。", "mock/m")


class _StubEvents:
    async def __call__(self, *args, **kwargs):
        return ([], "mock/m")


class _StubState:
    async def __call__(self, *args, **kwargs):
        return ({
            "state_deltas": [], "ledger_deltas": [],
            "emotion_deltas": [], "new_entities": [],
        }, "mock/m")


class TestMultiAgentPostCommitTrigger(unittest.IsolatedAsyncioTestCase):

    def setUp(self) -> None:
        self.db = _fresh_db()
        self._patches = []

    def tearDown(self) -> None:
        for p in self._patches:
            p.stop()

    async def test_persist_helper_writes_version_and_fires_pipeline(self) -> None:
        from ui.backend.app.services.commit_pipeline import (
            retry, sub_tasks, task_registry,
        )
        from ui.backend.app.services.commit_pipeline.sub_tasks import (
            summarizer as summarizer_mod,
            event_extractor as event_extractor_mod,
            state_extractor as state_extractor_mod,
        )
        retry.set_schedule_for_tests((0, 0, 0))
        task_registry.reset_for_tests()

        # Stub LLM calls inside sub-tasks (conftest.py only covers
        # tests/post_commit/; this integration test lives elsewhere).
        for p in (
            mock.patch.object(summarizer_mod, "_call_llm", _StubSummary()),
            mock.patch.object(event_extractor_mod, "_call_llm", _StubEvents()),
            mock.patch.object(state_extractor_mod, "_call_llm", _StubState()),
            mock.patch(
                "ui.backend.app.services.project_paths.get_db_path",
                return_value=self.db,
            ),
        ):
            p.start()
            self._patches.append(p)

        # Invoke the helper the way _run_pipeline_inner does.
        from ui.backend.app.routers.generation_api import (
            _persist_and_trigger_post_commit,
        )
        await _persist_and_trigger_post_commit(
            session_id="sess_test_xyz",
            req_data={
                "project_id": "p1",
                "chapter_id": "ch1",
                "chapter_num": 5,
            },
            final_text="第一章正文。主角进入幽冥谷。" * 20,
            eval_result={"score": 80, "passed": True},
        )

        # The fire-and-forget thread needs a moment to spin up + run
        # the (now sync-via-stub) sub-tasks. Poll up to 5s.
        for _ in range(100):
            with sqlite3.connect(self.db) as c:
                n_tasks = c.execute(
                    "SELECT COUNT(*) FROM commit_tasks"
                ).fetchone()[0]
                done = c.execute(
                    "SELECT COUNT(*) FROM commit_tasks "
                    "WHERE state IN ('completed','failed_needs_manual','skipped')"
                ).fetchone()[0]
            if n_tasks > 0 and done >= n_tasks:
                break
            await asyncio.sleep(0.05)

        # ── Assertions ──
        with sqlite3.connect(self.db) as c:
            c.row_factory = sqlite3.Row

            # 1. text_versions row exists. The source CHECK constraint
            # allows only ('ai','user_edit','rewrite'); the eval-pass
            # signal lives in model_used.
            ver_rows = list(c.execute(
                "SELECT chapter_id, source, model_used, content "
                "FROM text_versions WHERE chapter_id = ?", ("ch1",),
            ))
            self.assertEqual(len(ver_rows), 1, "text_versions not written")
            self.assertEqual(ver_rows[0]["source"], "ai")
            self.assertEqual(
                ver_rows[0]["model_used"], "multi_agent_pipeline.eval_passed",
            )
            self.assertGreater(len(ver_rows[0]["content"]), 100)

            # 2. commit_tasks created for every enabled sub-task.
            task_rows = list(c.execute(
                "SELECT task_type, state FROM commit_tasks "
                "WHERE chapter_id = ?", ("ch1",),
            ))
            enabled_types = {t.task_type for t in sub_tasks.enabled_sub_tasks()}
            persisted_types = {r["task_type"] for r in task_rows}
            self.assertEqual(persisted_types, enabled_types)

            # 3. All in terminal state (no orphan 'pending'/'running').
            for r in task_rows:
                self.assertIn(
                    r["state"],
                    ("completed", "failed_needs_manual", "skipped"),
                    f"{r['task_type']} stuck in state {r['state']}",
                )

    async def test_persist_helper_skips_when_no_project_id(self) -> None:
        """Ephemeral runs (UI preview etc.) shouldn't pollute the
        commit_tasks table — the helper bails out silently."""
        from ui.backend.app.routers.generation_api import (
            _persist_and_trigger_post_commit,
        )
        from ui.backend.app.services.commit_pipeline import task_registry
        task_registry.reset_for_tests()

        await _persist_and_trigger_post_commit(
            session_id="sess_ephemeral",
            req_data={"project_id": "", "chapter_id": ""},
            final_text="text",
            eval_result={"score": 50, "passed": False},
        )
        # No tasks should have been created.
        with sqlite3.connect(self.db) as c:
            n = c.execute("SELECT COUNT(*) FROM commit_tasks").fetchone()[0]
        self.assertEqual(n, 0)

    async def test_persist_helper_uses_eval_failed_source(self) -> None:
        """Evaluator marked the chapter as failed → version source
        reflects that so loaders can filter."""
        from ui.backend.app.routers.generation_api import (
            _persist_and_trigger_post_commit,
        )
        from ui.backend.app.services.commit_pipeline.sub_tasks import (
            summarizer as summarizer_mod,
            event_extractor as event_extractor_mod,
            state_extractor as state_extractor_mod,
        )
        for p in (
            mock.patch.object(summarizer_mod, "_call_llm", _StubSummary()),
            mock.patch.object(event_extractor_mod, "_call_llm", _StubEvents()),
            mock.patch.object(state_extractor_mod, "_call_llm", _StubState()),
            mock.patch(
                "ui.backend.app.services.project_paths.get_db_path",
                return_value=self.db,
            ),
        ):
            p.start()
            self._patches.append(p)

        await _persist_and_trigger_post_commit(
            session_id="sess_failed",
            req_data={
                "project_id": "p1", "chapter_id": "ch2", "chapter_num": 2,
            },
            final_text="generated text that failed evaluation",
            eval_result={"score": 30, "passed": False},
        )

        with sqlite3.connect(self.db) as c:
            row = c.execute(
                "SELECT source, model_used FROM text_versions WHERE chapter_id = ?",
                ("ch2",),
            ).fetchone()
        # Source is always 'ai' (CHECK constraint); model_used encodes
        # the eval verdict.
        self.assertEqual(row[0], "ai")
        self.assertEqual(row[1], "multi_agent_pipeline.eval_failed")


if __name__ == "__main__":
    unittest.main()
