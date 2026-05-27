"""Integration test for build_generation_context across all 14 loaders.

LOADER_SPEC Batch 6 finale. The test runs the full builder against a
fresh empty project + a project with one of every kind of data seeded.
It doesn't assert specific content; it asserts:

- the builder doesn't raise on any input
- every expected block key is present in the returned ``blocks`` dict
- excludes (``block::__all__``) silence the block they target
- the sections list is consistent with the blocks dict

This is the safety net for the larger refactor — when later batches
land more loaders, this test ensures the integration matrix stays
healthy.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from storage.project_schema import ensure_creation_tables
from ui.backend.app.services import (
    project_store, snapshot_store,
)
from ui.backend.app.services.prompt_context import builder
from ui.backend.app.services.prompt_context.builder import (
    build_generation_context,
)


EXPECTED_BLOCK_KEYS = {
    # System layer
    "platform_directive", "style_calibration",
    "reference_summary", "user_preferences",
    # Project static
    "character_cards", "worldbook", "chapter_outline",
    # Project dynamic
    "adjacent_context", "foreshadowing",
    "current_chapter_draft", "storyland_state",
    "subplots", "reader_memory",
    # Resources / context
    "writing_knowledge", "writing_skills",
    "inspiration", "skills", "reference",
}


def _fresh_db() -> str:
    db = os.path.join(tempfile.mkdtemp(), "int.db")
    con = sqlite3.connect(db)
    con.execute("PRAGMA foreign_keys=ON")
    ensure_creation_tables(con)
    con.execute("INSERT INTO projects (project_id, title) VALUES ('p1','t')")
    con.commit()
    con.close()
    return db


class _DBPath:
    """Patch every db-path lookup site that any loader uses."""
    def __init__(self, db: str) -> None:
        self._patches = [
            mock.patch(
                "ui.backend.app.services.project_paths.get_db_path",
                return_value=db,
            ),
            # The builder takes db_path explicitly via parameter, but
            # downstream loaders (worldbook, inspiration, etc.) look up
            # via project_paths.get_db_path.
        ]
        # Embedding backend isn't available in this env — patch the
        # shared helper everywhere so similarity layers fall back to
        # their default behavior without trying to load a model.
        self._patches.append(
            mock.patch(
                "ui.backend.app.services.prompt_context.utils.embed_sync",
                return_value=[],
            )
        )
        # ChromaDB-backed semantic memory also has to be stubbed out so
        # the reader_memory loader doesn't try to spin up the persistent
        # client during the test.
        self._patches.append(
            mock.patch(
                "ui.backend.app.services.prompt_context.loaders."
                "reader_memory._query_semantic",
                return_value=[],
            )
        )

    def __enter__(self):
        for p in self._patches:
            p.start()
        return self

    def __exit__(self, *exc):
        for p in self._patches:
            p.stop()


def _seed_minimal_chapter(db: str, project_id: str, chapter_id: str,
                          chapter_num: int) -> None:
    """Seed an editor doc + chapter row so chapter_fields finds it."""
    project_store.save_editor_doc(db, project_id, {
        "volumes": [{"chapters": [{
            "id": chapter_id, "chapter_id": chapter_id,
            "synopsis": "本章主角去幽冥谷找李清漪",
            "title": "前夜",
            "characters": ["张远", "李清漪"],
            "on_stage_entities": {
                "characters": ["张远", "李清漪"],
                "locations": ["幽冥谷"],
                "items": ["玉佩"],
                "organizations": ["玄阴宗"],
            },
            "content": "正文。\n\n第二段。",
        }]}],
    })


def _seed_full_dataset(db: str) -> str:
    """Populate the project with one row of every kind of data the
    builder might surface. Returns the seeded chapter_id."""
    _seed_minimal_chapter(db, "p1", "ch1", 1)
    # Character
    char = project_store.upsert_character(db, {
        "project_id": "p1", "name": "张远",
        "personality": "沉稳",
        "background": "考古少校副官",
    })
    project_store.upsert_character(db, {
        "project_id": "p1", "name": "李清漪",
        "personality": "聪慧",
    })
    # Snapshot to exercise the resolver
    snapshot_store.upsert_snapshot(db, {
        "character_id": char["id"], "project_id": "p1",
        "bound_chapters": [5, 7],
        "transition_complete_chapter": 7,
        "personality_override": "已转变",
    })
    # Worldbook entry
    project_store.upsert_worldbook(db, {
        "project_id": "p1", "title": "幽冥谷", "category": "地点",
        "content": "禁地",
    })
    # Subplot (main)
    with sqlite3.connect(db) as c:
        c.execute(
            "INSERT INTO subplot_threads(thread_id, project_id, name, "
            "description, status, thread_type, start_chapter) "
            "VALUES ('m1','p1','身世主线','x','building','main',1)"
        )
        c.execute(
            "INSERT INTO pending_hooks(hook_id, project_id, description, "
            "status, importance, origin_chapter) "
            "VALUES ('h1','p1','玉佩谜', 'open', 'A', 3)"
        )
        c.execute(
            "INSERT INTO truth_current_state(triple_id, project_id, subject, "
            "subject_type, predicate, object, valid_from_chapter, confidence) "
            "VALUES ('t1','p1','张远','character','在','青云山', 5, 1.0)"
        )
        c.execute(
            "INSERT INTO chapter_summaries(summary_id, project_id, "
            "chapter_num, summary_text, title, is_anchor) "
            "VALUES ('cs1','p1', 1, '主角离家', '启程', 0)"
        )
        c.execute(
            "INSERT INTO episodic_events(event_id, project_id, chapter_num, "
            "event_type, description, characters_json) "
            "VALUES ('e1','p1', 1, 'plot', '主角离家', '[\"张远\"]')"
        )
        c.commit()
    return "ch1"


class TestBuilderIntegration(unittest.TestCase):

    def test_empty_project_doesnt_raise_and_returns_block_keys(self) -> None:
        db = _fresh_db()
        with _DBPath(db):
            ctx = build_generation_context(
                "p1", chapter_num=1, characters=[], db_path=db,
                chapter_id="",
            )
        self.assertEqual(set(ctx["blocks"].keys()), EXPECTED_BLOCK_KEYS)
        # Every block on an empty project either is "" or has the
        # ``## <label>\n…`` shape.
        for k, v in ctx["blocks"].items():
            if v:
                self.assertTrue(
                    v.startswith("\n\n## ") or v.startswith("## "),
                    f"block {k} has unexpected shape: {v[:80]!r}",
                )

    def test_seeded_project_has_nonzero_token_estimate(self) -> None:
        db = _fresh_db()
        chapter_id = _seed_full_dataset(db)
        with _DBPath(db):
            ctx = build_generation_context(
                "p1", chapter_num=5, characters=["张远", "李清漪"],
                db_path=db, chapter_id=chapter_id,
            )
        self.assertGreater(ctx["token_estimate"], 0)
        # At least the static / outline blocks should be non-empty.
        self.assertNotEqual(ctx["blocks"]["chapter_outline"], "")
        # All 18 keys still there.
        self.assertEqual(set(ctx["blocks"].keys()), EXPECTED_BLOCK_KEYS)

    def test_exclude_all_silences_block(self) -> None:
        db = _fresh_db()
        chapter_id = _seed_full_dataset(db)
        with _DBPath(db):
            ctx = build_generation_context(
                "p1", chapter_num=5, characters=["张远"],
                db_path=db, chapter_id=chapter_id,
                rag_excludes=[
                    "chapter_outline::__all__",
                    "storyland_state::__all__",
                    "reader_memory::__all__",
                    "subplots::__all__",
                    "skills::__all__",
                    "reference::__all__",
                    "inspiration::__all__",
                    "current_chapter_draft::__all__",
                    "foreshadowing::__all__",
                ],
            )
        for k in ("chapter_outline", "storyland_state", "reader_memory",
                  "subplots", "skills", "reference", "inspiration",
                  "current_chapter_draft", "foreshadowing"):
            self.assertEqual(ctx["blocks"][k], "", f"{k} should be excluded")

    def test_sections_list_matches_nonempty_blocks(self) -> None:
        db = _fresh_db()
        chapter_id = _seed_full_dataset(db)
        with _DBPath(db):
            ctx = build_generation_context(
                "p1", chapter_num=5, characters=["张远"],
                db_path=db, chapter_id=chapter_id,
            )
        nonempty = sum(1 for v in ctx["blocks"].values() if v)
        self.assertEqual(len(ctx["sections"]), nonempty)
        for s in ctx["sections"]:
            self.assertIn("label", s)
            self.assertIn("content", s)
            self.assertTrue(s["label"].strip())

    def test_generation_mode_param_threads_through(self) -> None:
        """``generation_mode='continue'`` should produce a non-empty
        current_chapter_draft when there's content to tail off of."""
        db = _fresh_db()
        chapter_id = _seed_full_dataset(db)
        # save_editor_doc seeded chapter content; segment manager has
        # already split it into segments. Continue mode should pick up.
        with _DBPath(db):
            ctx = build_generation_context(
                "p1", chapter_num=5, characters=["张远"],
                db_path=db, chapter_id=chapter_id,
                generation_mode="continue",
            )
        self.assertNotEqual(ctx["blocks"]["current_chapter_draft"], "")


if __name__ == "__main__":
    unittest.main()
