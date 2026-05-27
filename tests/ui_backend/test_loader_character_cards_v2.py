"""Tests for the snapshot-aware character_cards loader (LOADER_SPEC Loader 5)."""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from storage.project_schema import ensure_creation_tables
from ui.backend.app.services import project_store, snapshot_store
from ui.backend.app.services.prompt_context.loaders import character_cards


def _fresh_db_with_char() -> tuple[str, str]:
    db = os.path.join(tempfile.mkdtemp(), "cc.db")
    con = sqlite3.connect(db)
    con.execute("PRAGMA foreign_keys=ON")
    ensure_creation_tables(con)
    con.execute("INSERT INTO projects (project_id, title) VALUES ('p1','t')")
    con.commit()
    con.close()
    char = project_store.upsert_character(db, {
        "project_id": "p1", "name": "李星河",
        "role": "主角",
        "appearance": "瘦削；眼神锐利",
        "personality": "沉稳寡言",
        "background": "考古世家",
        "speech_style": "言简意赅",
        "layer_b": {"loss_aversion": 1.5, "value_weights": {"survival": 0.4}},
    })
    return db, char["id"]


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


class TestStable(unittest.TestCase):

    def test_base_card_no_snapshot(self) -> None:
        db, _ = _fresh_db_with_char()
        with _DBPath(db):
            out = character_cards.load("p1", ["李星河"], chapter_num=5)
        self.assertIn("沉稳寡言", out)
        self.assertIn("考古世家", out)
        self.assertNotIn("[当前状态]", out)

    def test_baseline_from_completed_snapshot(self) -> None:
        db, cid = _fresh_db_with_char()
        snapshot_store.upsert_snapshot(db, {
            "character_id": cid, "project_id": "p1",
            "bound_chapters": [5, 7],
            "transition_complete_chapter": 7,
            "personality_override": "重新平静",
        })
        with _DBPath(db):
            out = character_cards.load("p1", ["李星河"], chapter_num=20)
        self.assertIn("重新平静", out)
        self.assertIn("[当前状态]", out)
        self.assertIn("自第 7 章起", out)


class TestTransitionEvent(unittest.TestCase):

    def test_event_renders_from_to_and_target_state(self) -> None:
        db, cid = _fresh_db_with_char()
        snapshot_store.upsert_snapshot(db, {
            "character_id": cid, "project_id": "p1",
            "bound_chapters": [28, 30, 32],
            "trigger_description": "得知身世",
            "personality_override": "阴沉",
        })
        with _DBPath(db):
            out = character_cards.load("p1", ["李星河"], chapter_num=30)
        self.assertIn("[正在转变]", out)
        self.assertIn("从 基线人设 到 Snapshot 1", out)
        self.assertIn("绑定章节：28, 30, 32（本章: 30）", out)
        self.assertIn("阴沉", out)


class TestTransitionGap(unittest.TestCase):

    def test_gap_says_unstable(self) -> None:
        db, cid = _fresh_db_with_char()
        snapshot_store.upsert_snapshot(db, {
            "character_id": cid, "project_id": "p1",
            "bound_chapters": [28, 30, 32],
            "personality_override": "阴沉",
        })
        with _DBPath(db):
            out = character_cards.load("p1", ["李星河"], chapter_num=29)
        self.assertIn("[transition 间歇期]", out)
        self.assertIn("不稳定中间状态", out)


class TestTransitionComplete(unittest.TestCase):

    def test_complete_calls_it_out(self) -> None:
        db, cid = _fresh_db_with_char()
        snapshot_store.upsert_snapshot(db, {
            "character_id": cid, "project_id": "p1",
            "bound_chapters": [28, 30, 32],
            "transition_complete_chapter": 32,
            "trigger_description": "得知身世",
            "personality_override": "阴沉新基线",
        })
        with _DBPath(db):
            out = character_cards.load("p1", ["李星河"], chapter_num=32)
        self.assertIn("[转变完成]", out)
        self.assertIn("阴沉新基线", out)


class TestRelationsAndAlias(unittest.TestCase):

    def test_relations_overrides_render(self) -> None:
        db, cid = _fresh_db_with_char()
        snapshot_store.upsert_snapshot(db, {
            "character_id": cid, "project_id": "p1",
            "bound_chapters": [10],
            "alias": "陈墨",
            "relations_overrides": {
                "李清漪": {
                    "label": "信任降低",
                    "sentiment_score": 60,
                    "trust_level": 50,
                },
            },
        })
        with _DBPath(db):
            out = character_cards.load("p1", ["李星河"], chapter_num=10)
        self.assertIn("化名：陈墨", out)
        self.assertIn("对李清漪", out)
        self.assertIn("sentiment 60", out)

    def test_hidden_identities_carried_under_reserved_key(self) -> None:
        db, cid = _fresh_db_with_char()
        snapshot_store.upsert_snapshot(db, {
            "character_id": cid, "project_id": "p1",
            "bound_chapters": [10],
            "relations_overrides": {
                "__hidden_identities__": [
                    {"name": "陈墨", "revealed_to": ["李清漪"],
                     "notes": "为复仇隐姓埋名"},
                ],
            },
        })
        with _DBPath(db):
            out = character_cards.load("p1", ["李星河"], chapter_num=10)
        self.assertIn("化名「陈墨」", out)
        self.assertIn("已知真相：李清漪", out)


class TestExcludeAndUnknown(unittest.TestCase):

    def test_exclude_drops_character(self) -> None:
        db, _ = _fresh_db_with_char()
        with _DBPath(db):
            out = character_cards.load(
                "p1", ["李星河"], exclude={"李星河"}, chapter_num=5,
            )
        self.assertEqual(out, "")

    def test_unknown_name_yields_empty(self) -> None:
        db, _ = _fresh_db_with_char()
        with _DBPath(db):
            self.assertEqual(
                character_cards.load("p1", ["不存在"], chapter_num=5), "",
            )


if __name__ == "__main__":
    unittest.main()
