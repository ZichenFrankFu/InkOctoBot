"""Tests for the storyland_state prompt-context loader (LOADER_SPEC Loader 10)."""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from storage.project_schema import ensure_creation_tables
from ui.backend.app.services.prompt_context.loaders import storyland_state as ss


def _fresh_db() -> str:
    db = os.path.join(tempfile.mkdtemp(), "ss.db")
    con = sqlite3.connect(db)
    con.execute("PRAGMA foreign_keys=ON")
    ensure_creation_tables(con)
    con.execute("INSERT INTO projects (project_id, title) VALUES ('p1','t')")
    con.commit()
    con.close()
    return db


def _add_state(db: str, *, subject: str, subject_type: str,
                predicate: str, object: str, valid_from: int) -> None:
    import uuid
    with sqlite3.connect(db) as c:
        c.execute(
            "INSERT INTO truth_current_state (triple_id, project_id, "
            "subject, subject_type, predicate, object, valid_from_chapter, "
            "confidence) VALUES (?, 'p1', ?, ?, ?, ?, ?, 1.0)",
            (f"tr_{uuid.uuid4().hex[:8]}", subject, subject_type,
             predicate, object, valid_from),
        )
        c.commit()


def _add_ledger(db: str, *, character: str, key: str, value: int,
                chapter: int, delta: int) -> None:
    import uuid
    with sqlite3.connect(db) as c:
        c.execute(
            "INSERT INTO character_ledger (ledger_id, project_id, "
            "character_name, chapter_num, category, key, operation, "
            "delta, new_value) VALUES (?, 'p1', ?, ?, 'resource', ?, ?, ?, ?)",
            (f"l_{uuid.uuid4().hex[:8]}", character, chapter, key,
             "add" if delta > 0 else "subtract", delta, value),
        )
        c.commit()


def _add_arc(db: str, *, character: str, from_state: str, to_state: str,
             trigger: str, chapter: int) -> None:
    import uuid
    with sqlite3.connect(db) as c:
        c.execute(
            "INSERT INTO emotion_arcs (arc_id, project_id, character_name, "
            "chapter_num, from_state, to_state, trigger) "
            "VALUES (?, 'p1', ?, ?, ?, ?, ?)",
            (f"a_{uuid.uuid4().hex[:8]}", character, chapter,
             from_state, to_state, trigger),
        )
        c.commit()


class _DBPath:
    def __init__(self, db: str) -> None:
        self._patcher = mock.patch(
            "ui.backend.app.services.project_paths.get_db_path",
            return_value=db,
        )

    def __enter__(self):
        self._patcher.start()
        return self

    def __exit__(self, *exc):
        self._patcher.stop()


class TestStorylandStateLoader(unittest.TestCase):

    def test_empty_returns_empty(self) -> None:
        db = _fresh_db()
        with _DBPath(db):
            self.assertEqual(ss.load("p1", 5, {}), "")

    def test_renders_character_state_filtered_by_on_stage(self) -> None:
        db = _fresh_db()
        _add_state(db, subject="张远", subject_type="character",
                    predicate="在", object="青云山", valid_from=3)
        _add_state(db, subject="李清漪", subject_type="character",
                    predicate="在", object="幽冥谷", valid_from=5)
        with _DBPath(db):
            out = ss.load("p1", 10, {"characters": ["张远"]})
        self.assertIn("角色当前位置 / 状态", out)
        self.assertIn("张远 在 青云山", out)
        self.assertNotIn("李清漪", out)  # not on stage → not shown

    def test_renders_all_four_entity_types(self) -> None:
        db = _fresh_db()
        _add_state(db, subject="张远", subject_type="character",
                    predicate="情绪", object="警觉", valid_from=5)
        _add_state(db, subject="幽冥谷", subject_type="location",
                    predicate="物理状态", object="山顶被打平", valid_from=6)
        _add_state(db, subject="玉佩", subject_type="item",
                    predicate="持有者", object="张远", valid_from=3)
        _add_state(db, subject="玄阴宗", subject_type="organization",
                    predicate="据点", object="北部寒冰山脉", valid_from=8)
        with _DBPath(db):
            out = ss.load("p1", 10, {
                "characters": ["张远"],
                "locations": ["幽冥谷"],
                "items": ["玉佩"],
                "organizations": ["玄阴宗"],
            })
        self.assertIn("角色当前位置 / 状态", out)
        self.assertIn("地点状态", out)
        self.assertIn("关键物品状态", out)
        self.assertIn("组织状态", out)
        self.assertIn("山顶被打平", out)
        self.assertIn("北部寒冰山脉", out)

    def test_ledger_table_rendered(self) -> None:
        db = _fresh_db()
        _add_ledger(db, character="张远", key="灵石", value=120, chapter=8, delta=50)
        with _DBPath(db):
            out = ss.load("p1", 10, {"characters": ["张远"]})
        self.assertIn("角色资源 ledger", out)
        self.assertIn("灵石", out)
        self.assertIn("120", out)

    def test_emotion_arcs_within_window(self) -> None:
        db = _fresh_db()
        # window default = 3, chapter_num=10 → looks at chapters [7,9]
        _add_arc(db, character="张远", from_state="困惑", to_state="警觉",
                 trigger="发现山被打平", chapter=8)
        _add_arc(db, character="张远", from_state="平静", to_state="困惑",
                 trigger="师父隐瞒", chapter=2)
        with _DBPath(db):
            out = ss.load("p1", 10, {"characters": ["张远"]})
        self.assertIn("情绪轨迹", out)
        self.assertIn("困惑 -> 警觉", out)
        self.assertNotIn("平静 -> 困惑", out)  # outside the 3-chapter window

    def test_exclude_drops_subject(self) -> None:
        db = _fresh_db()
        _add_state(db, subject="张远", subject_type="character",
                    predicate="在", object="青云山", valid_from=3)
        _add_state(db, subject="李清漪", subject_type="character",
                    predicate="在", object="幽冥谷", valid_from=5)
        with _DBPath(db):
            out = ss.load("p1", 10, {"characters": ["张远", "李清漪"]},
                           exclude={"张远"})
        self.assertNotIn("张远", out)
        self.assertIn("李清漪", out)


if __name__ == "__main__":
    unittest.main()
