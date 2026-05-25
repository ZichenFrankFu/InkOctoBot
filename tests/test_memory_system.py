"""
Tests for the 4-layer memory system.

Tests: ImmediateContext, ChapterBuffer, SemanticMemory (mock),
EpisodicTimeline, MemoryConsolidator, KnowledgeIsolationEngine, MemoryManager.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from storage.creation_schema import ensure_creation_tables
from rag.memory.immediate import ImmediateContext, SceneContext
from rag.memory.chapter_buffer import ChapterBuffer
from rag.memory.episodic_timeline import EpisodicTimeline


def _setup_db() -> str:
    """Create a temp DB with all creation tables + a test project."""
    db_path = os.path.join(tempfile.mkdtemp(), "test.db")
    conn = sqlite3.connect(db_path)
    ensure_creation_tables(conn)
    conn.execute("INSERT INTO projects (project_id, title) VALUES ('p1', 'TestProject')")
    conn.commit()
    conn.close()
    return db_path


class TestImmediateContext(unittest.TestCase):
    """Layer 1: Immediate Context tests."""

    def test_initial_state(self):
        ctx = ImmediateContext()
        self.assertEqual(ctx._chapter_num, 0)
        self.assertIsNone(ctx.current_scene)
        self.assertEqual(ctx.get_context_text(), "")

    def test_advance_scene(self):
        ctx = ImmediateContext()
        scene1 = SceneContext(
            scene_index=0, characters=["张远", "李清漪"],
            location="青云峰", time_marker="清晨",
        )
        ctx.advance_scene(scene1)
        self.assertEqual(ctx.current_scene.scene_index, 0)
        self.assertIn("张远", ctx.current_scene.characters)

        # Advance to scene 2 — scene 1 becomes previous
        scene2 = SceneContext(
            scene_index=1, characters=["张远"],
            location="密室", time_marker="午后",
        )
        ctx.advance_scene(scene2)
        self.assertEqual(ctx.current_scene.scene_index, 1)
        self.assertIsNotNone(ctx.previous_scene)
        self.assertEqual(ctx.previous_scene.scene_index, 0)

    def test_update_current(self):
        ctx = ImmediateContext()
        ctx.advance_scene(SceneContext(scene_index=0, characters=["A"]))
        ctx.update_current("第一段文字")
        text = ctx.get_context_text()
        self.assertIn("第一段文字", text)

    def test_get_for_actor_only_current(self):
        ctx = ImmediateContext()
        ctx.advance_scene(SceneContext(scene_index=0, characters=["A", "B"]))
        ctx.update_current("场景内容")
        # Actor should only see current scene
        actor_ctx = ctx.get_for_actor("A")
        self.assertIn("场景内容", actor_ctx)

    def test_set_chapter(self):
        ctx = ImmediateContext()
        ctx.set_chapter(5)
        self.assertEqual(ctx._chapter_num, 5)

    def test_to_dict(self):
        ctx = ImmediateContext()
        d = ctx.to_dict()
        self.assertIn("chapter_num", d)
        self.assertIn("has_current", d)


class TestChapterBuffer(unittest.TestCase):
    """Layer 2: Chapter Buffer tests."""

    def setUp(self):
        self.db_path = _setup_db()
        self.buffer = ChapterBuffer(self.db_path, max_chapters=3)

    def test_add_and_get(self):
        self.buffer.add_summary("p1", 1, "第一章摘要", ["事件A"], {"张远": "紧张"})
        summaries = self.buffer.get_active_summaries("p1")
        self.assertEqual(len(summaries), 1)
        self.assertEqual(summaries[0]["chapter_num"], 1)
        self.assertIn("事件A", summaries[0]["key_events_json"])

    def test_buffer_text(self):
        self.buffer.add_summary("p1", 1, "第一章：张远入门")
        self.buffer.add_summary("p1", 2, "第二章：首次修炼")
        text = self.buffer.get_buffer_text("p1")
        self.assertIn("第一章", text)
        self.assertIn("首次修炼", text)

    def test_overflow_detection(self):
        for i in range(5):
            self.buffer.add_summary("p1", i + 1, f"第{i+1}章摘要")
        self.assertTrue(self.buffer.needs_compression("p1"))
        overflow = self.buffer.get_overflow_summaries("p1")
        self.assertEqual(len(overflow), 2)  # 5 - max_chapters(3) = 2

    def test_deactivate(self):
        sid = self.buffer.add_summary("p1", 1, "摘要")
        self.buffer.deactivate_summary(sid)
        active = self.buffer.get_active_summaries("p1")
        self.assertEqual(len(active), 0)


class TestEpisodicTimeline(unittest.TestCase):
    """Layer 4: Episodic Timeline tests."""

    def setUp(self):
        self.db_path = _setup_db()
        self.timeline = EpisodicTimeline(self.db_path)

    def test_add_event(self):
        eid = self.timeline.add_event(
            "p1", 1, "plot", "张远觉醒灵根",
            characters=["张远"], importance=5,
        )
        self.assertTrue(eid.startswith("evt_"))

    def test_get_timeline(self):
        self.timeline.add_event("p1", 1, "plot", "事件A")
        self.timeline.add_event("p1", 2, "plot", "事件B")
        self.timeline.add_event("p1", 3, "revelation", "事件C")
        events = self.timeline.get_timeline("p1")
        self.assertEqual(len(events), 3)

    def test_foreshadowing_tracking(self):
        self.timeline.add_event(
            "p1", 1, "foreshadowing", "暗门背后的秘密",
            foreshadow_status="planted", importance=4,
        )
        self.timeline.add_event(
            "p1", 5, "foreshadowing", "暗门伏笔已解",
            foreshadow_status="resolved", importance=4,
        )
        unresolved = self.timeline.get_unresolved_foreshadowing("p1")
        self.assertEqual(len(unresolved), 1)
        self.assertIn("暗门背后", unresolved[0]["description"])

    def test_resolve_foreshadowing(self):
        eid = self.timeline.add_event(
            "p1", 1, "foreshadowing", "伏笔A",
            foreshadow_status="planted",
        )
        self.timeline.resolve_foreshadowing(eid, 5)
        unresolved = self.timeline.get_unresolved_foreshadowing("p1")
        self.assertEqual(len(unresolved), 0)

    def test_event_stats(self):
        self.timeline.add_event("p1", 1, "plot", "A")
        self.timeline.add_event("p1", 1, "foreshadowing", "B", foreshadow_status="planted")
        self.timeline.add_event("p1", 2, "character_change", "C", characters=["张远"])
        stats = self.timeline.get_event_stats("p1")
        self.assertEqual(stats["total_events"], 3)
        self.assertEqual(stats["unresolved_foreshadowing"], 1)

    def test_character_events(self):
        self.timeline.add_event("p1", 1, "character_change", "张远变强", characters=["张远"])
        self.timeline.add_event("p1", 2, "plot", "李清漪出场", characters=["李清漪"])
        events = self.timeline.get_character_events("p1", "张远")
        self.assertEqual(len(events), 1)


class TestProjectIsolation(unittest.TestCase):
    """Test that memory layers properly isolate between projects."""

    def setUp(self):
        self.db_path = _setup_db()
        conn = sqlite3.connect(self.db_path)
        conn.execute("INSERT INTO projects (project_id, title) VALUES ('p2', 'Project2')")
        conn.commit()
        conn.close()

    def test_buffer_isolation(self):
        buf = ChapterBuffer(self.db_path)
        buf.add_summary("p1", 1, "P1章节")
        buf.add_summary("p2", 1, "P2章节")
        p1 = buf.get_active_summaries("p1")
        p2 = buf.get_active_summaries("p2")
        self.assertEqual(len(p1), 1)
        self.assertEqual(len(p2), 1)
        self.assertIn("P1", p1[0]["summary_text"])
        self.assertIn("P2", p2[0]["summary_text"])

    def test_timeline_isolation(self):
        tl = EpisodicTimeline(self.db_path)
        tl.add_event("p1", 1, "plot", "P1事件")
        tl.add_event("p2", 1, "plot", "P2事件")
        self.assertEqual(len(tl.get_timeline("p1")), 1)
        self.assertEqual(len(tl.get_timeline("p2")), 1)


if __name__ == "__main__":
    unittest.main()
