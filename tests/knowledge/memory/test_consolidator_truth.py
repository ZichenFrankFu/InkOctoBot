"""
Tests for MemoryConsolidator → Truth Files emission (v2).

Verifies the consolidator:
  - emits StatePatch for permanent_facts
  - emits HookDelta(action='new') for active_foreshadowing
  - emits EmotionArcEntry for character_state_changes
  - applies via TruthFileStore.apply_deltas
  - does NOT write to permanent_facts table (was dropped in v2)
  - does NOT write foreshadow_status to episodic_events
  - still writes free-text mirrors to ChromaDB semantic store
"""
from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))

from storage.project_schema import ensure_creation_tables
from knowledge.memory.consolidator import MemoryConsolidator


def _make_db() -> str:
    db = os.path.join(tempfile.mkdtemp(), "consolidator.db")
    con = sqlite3.connect(db)
    con.execute("PRAGMA foreign_keys=ON")
    ensure_creation_tables(con)
    con.execute(
        "INSERT INTO projects (project_id, title) VALUES ('p1', 't')"
    )
    con.commit()
    con.close()
    return db


class _Resp:
    def __init__(self, content: str) -> None:
        self.content = content


class TestConsolidatorEmitsTruthDeltas(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self) -> None:
        self.db = _make_db()

        # Mock LLM router returns a stable JSON
        self.router = MagicMock()
        self.router.generate = AsyncMock(return_value=_Resp(json.dumps({
            "permanent_facts": ["李星河第3章获得了星门坐标"],
            "active_foreshadowing": ["格里芬的真实身份未揭示"],
            "character_state_changes": [{
                "character": "苏晚",
                "change": "从警惕到信任",
                "from_state": "警惕",
                "to_state": "信任",
            }],
        })))

        self.semantic = MagicMock()
        self.semantic.store = MagicMock(return_value="mem_id")

        # ChapterBuffer mock with one overflow summary
        self.buffer = MagicMock()
        self.buffer.get_overflow_summaries = MagicMock(return_value=[{
            "summary_id": "s1",
            "chapter_num": 3,
            "summary_text": "第三章的简短摘要",
        }])
        self.buffer.deactivate_summary = MagicMock()

        self.timeline = MagicMock()  # consolidator no longer writes here

        self.consolidator = MemoryConsolidator(
            router=self.router,
            chapter_buffer=self.buffer,
            semantic_memory=self.semantic,
            episodic_timeline=self.timeline,
            db_path=self.db,
        )

    async def test_truth_files_populated(self) -> None:
        processed = await self.consolidator.consolidate_if_needed("p1")
        self.assertEqual(processed, ["s1"])

        with sqlite3.connect(self.db) as c:
            c.row_factory = sqlite3.Row
            # StatePatch -> truth_current_state
            states = c.execute(
                "SELECT subject, predicate, object FROM truth_current_state "
                "WHERE project_id='p1'"
            ).fetchall()
            self.assertEqual(len(states), 1)
            self.assertEqual(states[0]["subject"], "叙事")
            self.assertIn("星门", states[0]["object"])

            # HookDelta -> pending_hooks
            hooks = c.execute(
                "SELECT description, status, importance "
                "FROM pending_hooks WHERE project_id='p1'"
            ).fetchall()
            self.assertEqual(len(hooks), 1)
            self.assertIn("格里芬", hooks[0]["description"])
            self.assertEqual(hooks[0]["status"], "open")
            self.assertEqual(hooks[0]["importance"], "B")

            # EmotionArcEntry -> emotion_arcs
            arcs = c.execute(
                "SELECT character_name, from_state, to_state, trigger "
                "FROM emotion_arcs WHERE project_id='p1'"
            ).fetchall()
            self.assertEqual(len(arcs), 1)
            self.assertEqual(arcs[0]["character_name"], "苏晚")
            self.assertEqual(arcs[0]["from_state"], "警惕")
            self.assertEqual(arcs[0]["to_state"], "信任")

    async def test_does_not_write_dropped_tables(self) -> None:
        await self.consolidator.consolidate_if_needed("p1")
        with sqlite3.connect(self.db) as c:
            tables = {r[0] for r in c.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()}
            # permanent_facts must not exist (dropped in v2).
            self.assertNotIn("permanent_facts", tables)

            # episodic_events doesn't have foreshadow columns either.
            cols = {r[1] for r in c.execute(
                "PRAGMA table_info(episodic_events)"
            )}
            self.assertNotIn("foreshadow_status", cols)

    async def test_semantic_store_not_mirrored(self) -> None:
        """v2.1 MEMORY_VS_TRUTH: consolidator no longer double-stores
        facts / foreshadowing into L3. Truth Files own that state."""
        await self.consolidator.consolidate_if_needed("p1")
        # No L3 writes from the structured-extraction path.
        self.semantic.store.assert_not_called()

    async def test_unparseable_response_falls_back_to_semantic(self) -> None:
        self.router.generate = AsyncMock(return_value=_Resp("not JSON"))
        await self.consolidator.consolidate_if_needed("p1")
        # Nothing in truth tables; one chapter_summary in semantic.
        with sqlite3.connect(self.db) as c:
            n = c.execute(
                "SELECT COUNT(*) FROM truth_current_state WHERE project_id='p1'"
            ).fetchone()[0]
        self.assertEqual(n, 0)
        self.semantic.store.assert_called_once()


class TestMemoryManagerForeshadow(unittest.TestCase):

    def setUp(self) -> None:
        self.db = _make_db()

    def test_get_unresolved_foreshadowing_reads_pending_hooks(self) -> None:
        # Insert two hooks directly.
        with sqlite3.connect(self.db) as c:
            c.execute(
                "INSERT INTO pending_hooks (hook_id, project_id, description, "
                "status, importance, origin_chapter) "
                "VALUES (?, ?, ?, 'open', 'A', 3)",
                ("h1", "p1", "未揭示的身份"),
            )
            c.execute(
                "INSERT INTO pending_hooks (hook_id, project_id, description, "
                "status, importance, origin_chapter) "
                "VALUES (?, ?, ?, 'resolved', 'B', 1)",
                ("h2", "p1", "已经回收的"),
            )
            c.commit()

        from knowledge.memory.manager import MemoryManager
        mgr = MemoryManager(db_path=self.db)
        mgr.set_project("p1")
        items = mgr.get_unresolved_foreshadowing()
        # Only the 'open' one returned.
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["description"], "未揭示的身份")
        self.assertEqual(items[0]["status"], "open")
        # Legacy alias preserved for old formatters.
        self.assertEqual(items[0]["foreshadow_status"], "open")


if __name__ == "__main__":
    unittest.main()
