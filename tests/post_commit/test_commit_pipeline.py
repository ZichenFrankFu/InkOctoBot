"""Tests for the post-commit pipeline framework (Phase 1)."""
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
from ui.backend.app.services.commit_pipeline import (
    pipeline, retry, sub_tasks, task_registry,
)


def _fresh_db() -> str:
    db = os.path.join(tempfile.mkdtemp(), "cp.db")
    con = sqlite3.connect(db)
    ensure_creation_tables(con)
    con.execute("INSERT INTO projects (project_id, title) VALUES ('p1', 't')")
    con.commit()
    con.close()
    return db


def _ctx(db: str, chapter_id: str = "ch1") -> sub_tasks.SubTaskContext:
    return sub_tasks.SubTaskContext(
        project_id="p1", chapter_id=chapter_id, db_path=db,
        chapter_text="测试章节内容", chapter_num=1,
    )


class TestStubsAllComplete(unittest.IsolatedAsyncioTestCase):

    def setUp(self) -> None:
        task_registry.reset_for_tests()
        retry.reset_for_tests()
        self.db = _fresh_db()

    def tearDown(self) -> None:
        task_registry.reset_for_tests()
        retry.reset_for_tests()

    async def test_pipeline_runs_every_enabled_subtask(self) -> None:
        """All default-enabled sub-tasks (5 of 6 — skill_emitter is
        opt-in) get a commit_tasks row and complete or skip — never
        stay in the running state once the pipeline returns."""
        task_ids = await pipeline.run_pipeline_sync(_ctx(self.db))
        self.assertEqual(len(task_ids), len(sub_tasks.enabled_sub_tasks()))
        rows = task_registry.list_for_chapter(self.db, "ch1")
        types = {r.task_type for r in rows}
        self.assertEqual(
            types, {t.task_type for t in sub_tasks.enabled_sub_tasks()},
        )
        for r in rows:
            # 'completed' or 'failed_*' — never pending/running. With
            # conftest's stubs everything should pass cleanly.
            self.assertEqual(r.state, "completed", f"{r.task_type}: {r.state}")


class TestTaskIsolation(unittest.IsolatedAsyncioTestCase):

    def setUp(self) -> None:
        task_registry.reset_for_tests()
        retry.reset_for_tests()
        # Zero-delay retries for fast tests.
        retry.set_schedule_for_tests((0, 0, 0))
        self.db = _fresh_db()

    def tearDown(self) -> None:
        task_registry.reset_for_tests()
        retry.reset_for_tests()

    async def test_one_subtask_failure_does_not_affect_others(self) -> None:
        """summarizer raises every attempt → goes to needs_manual.
        Other sub-tasks still complete normally."""
        from ui.backend.app.services.commit_pipeline.sub_tasks import (
            summarizer as summarizer_mod,
        )

        async def boom(ctx):
            raise RuntimeError("simulated failure")

        with mock.patch.object(summarizer_mod, "run", boom):
            await pipeline.run_pipeline_sync(_ctx(self.db))

        rows = {r.task_type: r for r in task_registry.list_for_chapter(self.db, "ch1")}
        self.assertEqual(rows["chapter_summarizer"].state, "failed_needs_manual")
        # Every other default-enabled task succeeded. (event_extractor
        # is default-disabled — its L4 events come from the
        # state_extractor merged call per Post-Commit·机制1.)
        others = {
            t.task_type for t in sub_tasks.enabled_sub_tasks()
        } - {"chapter_summarizer"}
        for t in others:
            self.assertEqual(rows[t].state, "completed", t)


class TestRetrySchedule(unittest.IsolatedAsyncioTestCase):

    def setUp(self) -> None:
        task_registry.reset_for_tests()
        retry.reset_for_tests()
        retry.set_schedule_for_tests((0, 0, 0))
        self.db = _fresh_db()

    def tearDown(self) -> None:
        task_registry.reset_for_tests()
        retry.reset_for_tests()

    async def test_succeeds_on_third_attempt(self) -> None:
        """First two attempts raise, third returns OK. Final state =
        completed, retry_count reflects the failed attempts."""
        from ui.backend.app.services.commit_pipeline.sub_tasks import (
            summarizer as summarizer_mod,
        )
        attempts = {"n": 0}

        async def flaky(ctx):
            attempts["n"] += 1
            if attempts["n"] < 3:
                raise RuntimeError(f"attempt {attempts['n']} failed")
            return {"status": "ok"}

        with mock.patch.object(summarizer_mod, "run", flaky):
            await pipeline.run_pipeline_sync(_ctx(self.db))

        rows = {r.task_type: r for r in task_registry.list_for_chapter(self.db, "ch1")}
        sm = rows["chapter_summarizer"]
        self.assertEqual(sm.state, "completed")
        # state machine: retry_count reflects the LAST recorded failure
        # before the success; the final write was a 'completed' update
        # which doesn't touch retry_count, so it stays at the previous
        # failed-attempt index.
        self.assertGreaterEqual(sm.retry_count, 2)
        self.assertEqual(attempts["n"], 3)

    async def test_exhausted_retries_flip_to_needs_manual(self) -> None:
        """3 retries (= 4 total attempts) all fail → failed_needs_manual."""
        from ui.backend.app.services.commit_pipeline.sub_tasks import (
            summarizer as summarizer_mod,
        )
        attempts = {"n": 0}

        async def always_fail(ctx):
            attempts["n"] += 1
            raise RuntimeError(f"attempt {attempts['n']} failed")

        with mock.patch.object(summarizer_mod, "run", always_fail):
            await pipeline.run_pipeline_sync(_ctx(self.db))

        rows = {r.task_type: r for r in task_registry.list_for_chapter(self.db, "ch1")}
        sm = rows["chapter_summarizer"]
        self.assertEqual(sm.state, "failed_needs_manual")
        self.assertEqual(attempts["n"], retry.max_retries() + 1)
        self.assertTrue(sm.user_notified)


class TestNotificationOnExhausted(unittest.IsolatedAsyncioTestCase):

    def setUp(self) -> None:
        task_registry.reset_for_tests()
        retry.reset_for_tests()
        retry.set_schedule_for_tests((0, 0, 0))
        self.db = _fresh_db()

    def tearDown(self) -> None:
        task_registry.reset_for_tests()
        retry.reset_for_tests()

    async def test_failed_task_creates_user_notification(self) -> None:
        from ui.backend.app.services.commit_pipeline.sub_tasks import (
            summarizer as summarizer_mod,
        )

        async def fail(ctx):
            raise RuntimeError("disk full")

        with mock.patch.object(summarizer_mod, "run", fail), \
             mock.patch(
                 "ui.backend.app.services.project_paths.get_db_path",
                 return_value=self.db,
             ):
            await pipeline.run_pipeline_sync(_ctx(self.db))

        # Verify a notification row exists.
        from ui.backend.app.services.notifications import list_notifications
        notifs = list_notifications(self.db, project_id="p1")
        summarizer_notifs = [n for n in notifs
                              if n.notification_type == "chapter_summarizer_failed"]
        self.assertEqual(len(summarizer_notifs), 1)
        n = summarizer_notifs[0]
        self.assertEqual(n.severity, "high")
        self.assertIn("disk full", n.description)
        self.assertEqual(n.action_url, "/chapters/ch1/edit-summary")


class TestFireAndForget(unittest.TestCase):
    """fire_and_forget_from_handler must NEVER raise to the caller."""

    def setUp(self) -> None:
        task_registry.reset_for_tests()
        retry.reset_for_tests()
        self.db = _fresh_db()

    def tearDown(self) -> None:
        task_registry.reset_for_tests()
        retry.reset_for_tests()

    def test_sync_handler_path_schedules_in_thread(self) -> None:
        """Called from a sync context (no event loop): spawns a daemon
        thread with its own loop. Returns None immediately."""
        result = pipeline.fire_and_forget_from_handler(
            project_id="p1", chapter_id="ch1", db_path=self.db,
        )
        self.assertIsNone(result)
        # Give the thread a moment to insert task rows.
        import time
        for _ in range(50):
            rows = task_registry.list_for_chapter(self.db, "ch1")
            if rows:
                break
            time.sleep(0.05)
        # At minimum the rows are queued — completion may still be in
        # flight in the background thread.
        rows = task_registry.list_for_chapter(self.db, "ch1")
        self.assertGreater(len(rows), 0)


if __name__ == "__main__":
    unittest.main()
