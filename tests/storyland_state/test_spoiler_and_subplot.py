"""
Tests for InkOS A1 (spoiler filter) + A4 (subplot board) integration.

A1: pending_hooks.is_spoiler + revealed_to_chars_json columns,
    HookDelta.is_spoiler + revealed_to_chars field,
    render_pending_hooks(pov_character=...) hides un-revealed spoilers.

A4: SubplotUpdate flows through extract_state_deltas + applies to
    subplot_threads table.
"""
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
from knowledge.storyland_state.schemas import (
    HookDelta, HookImportance, SubplotStatus, SubplotUpdate, StorylandStateDeltas,
)
from knowledge.storyland_state.store import StorylandStateStore
from knowledge.storyland_state.markdown_renderer import render_pending_hooks
from agents.production import storyland_state_integration as ti


def _fresh_db() -> str:
    db = os.path.join(tempfile.mkdtemp(), "spoiler.db")
    con = sqlite3.connect(db)
    con.execute("PRAGMA foreign_keys=ON")
    ensure_creation_tables(con)
    con.execute("INSERT INTO projects (project_id, title) VALUES ('p1', 't')")
    con.commit()
    con.close()
    return db


# ─────────────── A1: spoiler filter ─────────────────────────────────


class TestSpoilerFlagPersistence(unittest.TestCase):

    def test_is_spoiler_column_exists(self) -> None:
        db = _fresh_db()
        with sqlite3.connect(db) as c:
            cols = {r[1] for r in c.execute("PRAGMA table_info(pending_hooks)")}
        self.assertIn("is_spoiler", cols)
        self.assertIn("revealed_to_chars_json", cols)

    def test_hook_delta_with_spoiler_persists_flag(self) -> None:
        db = _fresh_db()
        store = StorylandStateStore("p1", db_path=db)
        res = store.apply_deltas(StorylandStateDeltas(
            chapter_num=1,
            hook_deltas=[HookDelta(
                description="格里芬的真实身份",
                action="new",
                importance=HookImportance.A,
                is_spoiler=True,
                revealed_to_chars=["叙述者"],
            )],
        ))
        self.assertTrue(res.success)
        with sqlite3.connect(db) as c:
            row = c.execute(
                "SELECT description, is_spoiler, revealed_to_chars_json "
                "FROM pending_hooks WHERE project_id='p1'"
            ).fetchone()
        self.assertIn("格里芬", row[0])
        self.assertEqual(row[1], 1)
        self.assertEqual(json.loads(row[2]), ["叙述者"])


class TestRenderSpoilerFilter(unittest.TestCase):

    def test_pov_none_shows_all(self) -> None:
        hooks = [
            {"hook_id": "h1", "description": "公开伏笔", "status": "open",
             "importance": "A", "origin_chapter": 1, "is_spoiler": 0,
             "revealed_to_chars_json": "[]"},
            {"hook_id": "h2", "description": "剧透伏笔", "status": "open",
             "importance": "A", "origin_chapter": 1, "is_spoiler": 1,
             "revealed_to_chars_json": "[\"叙述者\"]"},
        ]
        md = render_pending_hooks(
            hooks, project_id="p1", chapter_pointer=5, pov_character=None,
        )
        self.assertIn("公开伏笔", md)
        self.assertIn("剧透伏笔", md)

    def test_pov_filters_unrevealed_spoiler(self) -> None:
        hooks = [
            {"hook_id": "h1", "description": "公开伏笔", "status": "open",
             "importance": "A", "origin_chapter": 1, "is_spoiler": 0,
             "revealed_to_chars_json": "[]"},
            {"hook_id": "h2", "description": "剧透伏笔", "status": "open",
             "importance": "A", "origin_chapter": 1, "is_spoiler": 1,
             "revealed_to_chars_json": "[\"叙述者\"]"},
        ]
        md = render_pending_hooks(
            hooks, project_id="p1", chapter_pointer=5,
            pov_character="李星河",
        )
        self.assertIn("公开伏笔", md)
        self.assertNotIn("剧透伏笔", md)

    def test_pov_in_reveal_list_sees_spoiler(self) -> None:
        hooks = [
            {"hook_id": "h2", "description": "剧透伏笔", "status": "open",
             "importance": "A", "origin_chapter": 1, "is_spoiler": 1,
             "revealed_to_chars_json": "[\"格里芬\"]"},
        ]
        md_griffin = render_pending_hooks(
            hooks, project_id="p1", chapter_pointer=5,
            pov_character="格里芬",
        )
        md_klein = render_pending_hooks(
            hooks, project_id="p1", chapter_pointer=5,
            pov_character="克莱因",
        )
        self.assertIn("剧透伏笔", md_griffin)
        self.assertNotIn("剧透伏笔", md_klein)
        # Empty-result body has a note about hidden spoilers
        self.assertIn("spoiler hooks hidden", md_klein)


class TestBuildTruthContextSpoilerPlumbing(unittest.TestCase):
    """Verify spoiler filter plumbs through to storyland_state_integration."""

    def test_pov_character_passes_through(self) -> None:
        db = _fresh_db()
        store = StorylandStateStore("p1", db_path=db)
        store.apply_deltas(StorylandStateDeltas(
            chapter_num=1,
            hook_deltas=[
                HookDelta(description="A 知道的事", action="new", importance=HookImportance.B),
                HookDelta(description="A 不知道的事", action="new", importance=HookImportance.A,
                          is_spoiler=True, revealed_to_chars=["B"]),
            ],
        ))
        # Omniscient view sees both
        all_view = ti.build_state_context("p1", db, chapter_num=2, pov_character=None)
        self.assertIn("A 知道的事", all_view)
        self.assertIn("A 不知道的事", all_view)

        # POV=A only sees the public one
        a_view = ti.build_state_context(
            "p1", db, chapter_num=2, characters=["A"], pov_character="A",
        )
        self.assertIn("A 知道的事", a_view)
        self.assertNotIn("A 不知道的事", a_view)

        # POV=B sees both
        b_view = ti.build_state_context(
            "p1", db, chapter_num=2, characters=["B"], pov_character="B",
        )
        self.assertIn("A 不知道的事", b_view)


# ─────────────── A4: subplot board ──────────────────────────────────


class TestSubplotExtraction(unittest.IsolatedAsyncioTestCase):

    async def test_subplot_update_parsed_from_settlement(self) -> None:
        router = MagicMock()
        router.generate = AsyncMock()

        class _Resp:
            content = json.dumps({
                "subplot_updates": [
                    {"name": "宗门之争", "action": "advance",
                     "status_after": "building",
                     "related_hook_ids": [],
                     "note": "本章李星河公开站队"},
                ],
            })
        router.generate.return_value = _Resp()

        deltas = await ti.extract_state_deltas(
            router, "章节正文",
            chapter_num=5,
        )
        self.assertIsNotNone(deltas)
        self.assertEqual(len(deltas.subplot_updates), 1)
        su = deltas.subplot_updates[0]
        self.assertEqual(su.name, "宗门之争")
        self.assertEqual(su.action, "advance")
        self.assertEqual(su.status_after, SubplotStatus.building)
        self.assertEqual(su.note, "本章李星河公开站队")

    async def test_subplot_update_applies_to_db(self) -> None:
        db = _fresh_db()
        router = MagicMock()
        router.generate = AsyncMock()

        class _Resp:
            content = json.dumps({
                "subplot_updates": [
                    {"name": "宗门之争", "action": "new",
                     "status_after": "setup",
                     "related_hook_ids": [],
                     "note": "第 5 章引入"},
                ],
            })
        router.generate.return_value = _Resp()

        result = await ti.extract_and_apply_state_deltas(
            router, "正文",
            project_id="p1", db_path=db, chapter_num=5,
        )
        self.assertTrue(result["success"], result)
        with sqlite3.connect(db) as c:
            rows = c.execute(
                "SELECT name, status FROM subplot_threads WHERE project_id='p1'"
            ).fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], "宗门之争")
        self.assertEqual(rows[0][1], "setup")

    async def test_invalid_subplot_status_coerced(self) -> None:
        router = MagicMock()
        router.generate = AsyncMock()

        class _Resp:
            content = json.dumps({
                "subplot_updates": [
                    {"name": "x", "status_after": "bogus_status"},
                ],
            })
        router.generate.return_value = _Resp()
        deltas = await ti.extract_state_deltas(router, "x", chapter_num=1)
        self.assertEqual(deltas.subplot_updates[0].status_after, SubplotStatus.building)


if __name__ == "__main__":
    unittest.main()
