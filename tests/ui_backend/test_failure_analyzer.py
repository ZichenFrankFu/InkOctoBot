"""Unit tests for ui/backend/app/services/failure_analyzer.py."""
from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from storage.project_schema import ensure_creation_tables
from ui.backend.app.services import failure_analyzer as fa


def _fresh_db_with_chapter() -> tuple[str, str]:
    db = os.path.join(tempfile.mkdtemp(), "fa.db")
    con = sqlite3.connect(db)
    con.execute("PRAGMA foreign_keys=ON")
    ensure_creation_tables(con)
    con.execute("INSERT INTO projects (project_id, title) VALUES ('p1','t')")
    con.execute(
        "INSERT INTO chapters (chapter_id, project_id, chapter_num, volume) "
        "VALUES ('ch1','p1',1,1)"
    )
    con.commit()
    con.close()
    return db, "ch1"


class _MockResp:
    def __init__(self, content: str) -> None:
        self.content = content


class TestArchive(unittest.TestCase):

    def setUp(self) -> None:
        self.db, self.cid = _fresh_db_with_chapter()

    def test_archive_returns_id_and_persists(self) -> None:
        fid = fa.archive_failed_generation(
            self.db, self.cid, "p1", "失败正文" * 20, rejection_reason="reword",
        )
        self.assertTrue(fid.startswith("fg_"))
        got = fa.get_failed_generation(self.db, fid)
        self.assertEqual(got["rejection_reason"], "reword")
        self.assertEqual(got["issues_detected"], [])
        self.assertIn("失败", got["content_snippet"])

    def test_get_recent_orders_newest_first(self) -> None:
        # Force two distinct timestamps by inserting directly so the
        # ORDER BY rejected_at DESC has something to sort on.
        with sqlite3.connect(self.db) as con:
            con.execute(
                "INSERT INTO chapter_failed_generations "
                "(failed_gen_id, chapter_id, project_id, full_content, "
                "rejected_at, issues_detected) VALUES "
                "('a','ch1','p1','old','2020-01-01 00:00:00','[]')"
            )
            con.execute(
                "INSERT INTO chapter_failed_generations "
                "(failed_gen_id, chapter_id, project_id, full_content, "
                "rejected_at, issues_detected) VALUES "
                "('b','ch1','p1','new','2025-01-01 00:00:00','[]')"
            )
            con.commit()
        rows = fa.get_recent_failed_generations(self.db, self.cid, limit=5)
        self.assertEqual(rows[0]["failed_gen_id"], "b")
        self.assertEqual(rows[1]["failed_gen_id"], "a")

    def test_get_recent_respects_limit(self) -> None:
        for _ in range(5):
            fa.archive_failed_generation(self.db, self.cid, "p1", "x")
        rows = fa.get_recent_failed_generations(self.db, self.cid, limit=2)
        self.assertEqual(len(rows), 2)


class TestAnalyze(unittest.IsolatedAsyncioTestCase):

    def setUp(self) -> None:
        self.db, self.cid = _fresh_db_with_chapter()
        self.fid = fa.archive_failed_generation(
            self.db, self.cid, "p1", "失败的章节正文，开头是内心独白……",
        )

    async def test_analyze_persists_issues_on_success(self) -> None:
        router = MagicMock()
        router.generate = AsyncMock(return_value=_MockResp(json.dumps({
            "issues": [
                "开场用了内心独白，节奏拖沓",
                "对话太露骨",
                "配角戏份过多",
            ],
        })))
        out = await fa.analyze_failure(router, self.db, self.fid)
        self.assertEqual(len(out), 3)
        self.assertIn("内心独白", out[0])
        # Persistence check
        got = fa.get_failed_generation(self.db, self.fid)
        self.assertEqual(got["issues_detected"], out)

    async def test_analyze_swallows_llm_error(self) -> None:
        router = MagicMock()
        router.generate = AsyncMock(side_effect=RuntimeError("model timeout"))
        out = await fa.analyze_failure(router, self.db, self.fid)
        self.assertEqual(out, [])
        got = fa.get_failed_generation(self.db, self.fid)
        self.assertEqual(got["issues_detected"], [])

    async def test_analyze_handles_unparseable(self) -> None:
        router = MagicMock()
        router.generate = AsyncMock(return_value=_MockResp("not json at all"))
        out = await fa.analyze_failure(router, self.db, self.fid)
        self.assertEqual(out, [])

    async def test_analyze_handles_fenced_json(self) -> None:
        router = MagicMock()
        router.generate = AsyncMock(return_value=_MockResp(
            'Here you go:\n```json\n{"issues": ["问题一"]}\n```'
        ))
        out = await fa.analyze_failure(router, self.db, self.fid)
        self.assertEqual(out, ["问题一"])

    async def test_analyze_idempotent_reuses_stored_issues(self) -> None:
        router = MagicMock()
        router.generate = AsyncMock(return_value=_MockResp(
            json.dumps({"issues": ["x"]})
        ))
        # First call burns tokens
        await fa.analyze_failure(router, self.db, self.fid)
        self.assertEqual(router.generate.await_count, 1)
        # Second call must NOT re-query the LLM
        out = await fa.analyze_failure(router, self.db, self.fid)
        self.assertEqual(out, ["x"])
        self.assertEqual(router.generate.await_count, 1)

    async def test_analyze_missing_id_returns_empty(self) -> None:
        router = MagicMock()
        router.generate = AsyncMock()
        out = await fa.analyze_failure(router, self.db, "nope")
        self.assertEqual(out, [])
        router.generate.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
