"""Tests for ui/backend/app/services/snapshot_auto_detector.py."""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from storage.project_schema import ensure_creation_tables
from ui.backend.app.services import (
    project_store, snapshot_auto_detector as detector, snapshot_store,
)


def _fresh_db_with_char() -> tuple[str, str]:
    db = os.path.join(tempfile.mkdtemp(), "ad.db")
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


class _Resp:
    def __init__(self, content: str) -> None:
        self.content = content


class TestAutoDetector(unittest.IsolatedAsyncioTestCase):

    def setUp(self) -> None:
        self.db, self.cid = _fresh_db_with_char()
        self.snap = snapshot_store.upsert_snapshot(self.db, {
            "character_id": self.cid, "project_id": "p1",
            "expected_chapter_range_start": 5,
            "trigger_description": "得知父亲是神秘组织遗孤",
        })

    def _router(self, *, content: str | None = None,
                exc: Exception | None = None) -> MagicMock:
        router = MagicMock()
        if exc is not None:
            router.generate = AsyncMock(side_effect=exc)
        else:
            router.generate = AsyncMock(return_value=_Resp(content or ""))
        return router

    async def test_triggered_with_high_confidence_returns_candidate(self) -> None:
        router = self._router(content=json.dumps({
            "triggered": True, "confidence": 0.9, "reason": "明确出现",
        }))
        out = await detector.detect_after_chapter_commit(
            router, self.db, "p1", chapter_num=7,
            chapter_content="他得知自己父亲其实是神秘组织遗孤……",
            on_stage_characters=["李星河"],
        )
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["snapshot_id"], self.snap["snapshot_id"])
        self.assertEqual(out[0]["chapter_num"], 7)

    async def test_low_confidence_dropped(self) -> None:
        router = self._router(content=json.dumps({
            "triggered": True, "confidence": 0.5, "reason": "看起来像",
        }))
        out = await detector.detect_after_chapter_commit(
            router, self.db, "p1", chapter_num=7, chapter_content="x",
            on_stage_characters=["李星河"],
        )
        self.assertEqual(out, [])

    async def test_not_triggered_dropped(self) -> None:
        router = self._router(content=json.dumps({
            "triggered": False, "confidence": 0.99, "reason": "无",
        }))
        out = await detector.detect_after_chapter_commit(
            router, self.db, "p1", chapter_num=7, chapter_content="x",
            on_stage_characters=["李星河"],
        )
        self.assertEqual(out, [])

    async def test_chapter_before_expected_start_skipped(self) -> None:
        router = self._router(content=json.dumps({
            "triggered": True, "confidence": 0.9,
        }))
        out = await detector.detect_after_chapter_commit(
            router, self.db, "p1", chapter_num=3, chapter_content="x",
            on_stage_characters=["李星河"],
        )
        self.assertEqual(out, [])
        router.generate.assert_not_awaited()

    async def test_already_bound_chapter_skipped(self) -> None:
        snapshot_store.bind_chapter(self.db, self.snap["snapshot_id"], 7)
        router = self._router(content=json.dumps({
            "triggered": True, "confidence": 0.9,
        }))
        out = await detector.detect_after_chapter_commit(
            router, self.db, "p1", chapter_num=7, chapter_content="x",
            on_stage_characters=["李星河"],
        )
        self.assertEqual(out, [])
        router.generate.assert_not_awaited()

    async def test_llm_exception_returns_empty(self) -> None:
        router = self._router(exc=RuntimeError("boom"))
        out = await detector.detect_after_chapter_commit(
            router, self.db, "p1", chapter_num=7, chapter_content="x",
            on_stage_characters=["李星河"],
        )
        self.assertEqual(out, [])

    async def test_unparseable_response_returns_empty(self) -> None:
        router = self._router(content="not json at all")
        out = await detector.detect_after_chapter_commit(
            router, self.db, "p1", chapter_num=7, chapter_content="x",
            on_stage_characters=["李星河"],
        )
        self.assertEqual(out, [])

    async def test_fenced_response_parses(self) -> None:
        router = self._router(content=(
            'Here is the JSON:\n```json\n'
            '{"triggered": true, "confidence": 0.95}\n```'
        ))
        out = await detector.detect_after_chapter_commit(
            router, self.db, "p1", chapter_num=7, chapter_content="x",
            on_stage_characters=["李星河"],
        )
        self.assertEqual(len(out), 1)


class TestAutoBind(unittest.TestCase):

    def setUp(self) -> None:
        self.db, self.cid = _fresh_db_with_char()
        self.snap = snapshot_store.upsert_snapshot(self.db, {
            "character_id": self.cid, "project_id": "p1",
        })

    def test_auto_bind_adds_chapter(self) -> None:
        out = detector.auto_bind(self.db, self.snap["snapshot_id"], 7)
        self.assertIn(7, out["bound_chapters"])
        self.assertEqual(out["bound_by"], "auto")

    def test_auto_bind_skips_user_managed_snapshots(self) -> None:
        # Mark as user-managed first.
        snapshot_store.bind_chapter(
            self.db, self.snap["snapshot_id"], 5, source="user",
        )
        before = snapshot_store.get_snapshot(self.db, self.snap["snapshot_id"])
        out = detector.auto_bind(self.db, self.snap["snapshot_id"], 7)
        # auto_bind returns the unchanged user-bound snapshot.
        self.assertEqual(out["bound_chapters"], before["bound_chapters"])
        self.assertEqual(out["bound_by"], "user")

    def test_auto_bind_returns_none_for_unknown(self) -> None:
        self.assertIsNone(detector.auto_bind(self.db, "nope", 1))


if __name__ == "__main__":
    unittest.main()
