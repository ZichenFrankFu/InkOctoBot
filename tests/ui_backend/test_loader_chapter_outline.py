"""Tests for the chapter_outline prompt-context loader (LOADER_SPEC Loader 7)."""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from storage.project_schema import ensure_creation_tables
from ui.backend.app.services import project_store
from ui.backend.app.services.prompt_context.loaders import chapter_outline as co


def _fresh_db() -> str:
    db = os.path.join(tempfile.mkdtemp(), "co.db")
    con = sqlite3.connect(db)
    con.execute("PRAGMA foreign_keys=ON")
    ensure_creation_tables(con)
    con.execute("INSERT INTO projects (project_id, title) VALUES ('p1','t')")
    con.commit()
    con.close()
    return db


class TestChapterOutlineLoader(unittest.TestCase):

    def setUp(self) -> None:
        self.db = _fresh_db()
        self._patcher = mock.patch(
            "ui.backend.app.services.project_paths.get_db_path",
            return_value=self.db,
        )
        self._patcher.start()

    def tearDown(self) -> None:
        self._patcher.stop()

    def test_empty_chapter_id_returns_empty(self) -> None:
        self.assertEqual(co.load("p1", ""), "")

    def test_unknown_chapter_id_returns_empty(self) -> None:
        # No editor doc seeded → loader returns "".
        self.assertEqual(co.load("p1", "ch_nope"), "")

    def test_synopsis_only(self) -> None:
        project_store.save_editor_doc(self.db, "p1", {
            "volumes": [{"chapters": [{
                "id": "ch1", "chapter_id": "ch1",
                "synopsis": "张远独自进幽冥谷",
                "characters": [],
            }]}],
        })
        out = co.load("p1", "ch1")
        self.assertIn("本章大纲", out)
        self.assertIn("张远独自进幽冥谷", out)
        # No time/location/entities → no other sub-sections.
        self.assertNotIn("时间地点", out)

    def test_full_block_renders_all_envelope_types(self) -> None:
        project_store.save_editor_doc(self.db, "p1", {
            "volumes": [{"chapters": [{
                "id": "ch1", "chapter_id": "ch1",
                "synopsis": "决战幽冥谷",
                "time": "神武 5012 春",
                "location": "幽冥谷主洞",
                "characters": ["张远", "李清漪"],
                "on_stage_entities": {
                    "characters": ["张远", "李清漪"],
                    "locations": ["幽冥谷", "青云山"],
                    "items": ["玉佩"],
                    "organizations": ["玄阴宗"],
                },
            }]}],
        })
        out = co.load("p1", "ch1")
        self.assertIn("决战幽冥谷", out)
        # 时间 / 地点 / 出场角色 are intentionally NOT in chapter_outline —
        # they have dedicated blocks in single_agent_vars (time_location /
        # characters_block), so 大纲 only carries non-character entities.
        self.assertNotIn("时间：神武 5012 春", out)
        self.assertNotIn("地点：幽冥谷主洞", out)
        self.assertNotIn("出场角色：", out)
        self.assertNotIn("### 时间地点", out)
        self.assertIn("涉及地点：幽冥谷, 青云山", out)
        self.assertIn("涉及物品：玉佩", out)
        self.assertIn("涉及组织：玄阴宗", out)

    def test_missing_synopsis_shows_placeholder(self) -> None:
        project_store.save_editor_doc(self.db, "p1", {
            "volumes": [{"chapters": [{
                "id": "ch1", "chapter_id": "ch1",
                "characters": ["张远"],
            }]}],
        })
        out = co.load("p1", "ch1")
        self.assertIn("（暂无大纲）", out)


if __name__ == "__main__":
    unittest.main()
