"""
Tests for scripts/migrate_to_v2_schema.py.

Builds a synthetic data/ tree with one of every JSON file shape, runs
migrate, and asserts the expected rows landed in the right tables.
Also checks that re-running is a no-op (idempotency).
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from scripts.migrate_to_v2_schema import run_migration


def _write(p: Path, obj) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, ensure_ascii=False), "utf-8")


def _make_data_tree(root: Path) -> None:
    pid = "proj_demo"
    _write(root / "projects" / f"{pid}.json", {"id": pid, "name": "演示项目"})
    _write(root / "characters" / "char_a.json", {
        "id": "char_a", "project_id": pid, "name": "李星河",
        "role": "主角", "description": "...",
        "tags": ["主角"],
    })
    _write(root / "characters" / "char_b.json", {
        "id": "char_b", "project_id": pid, "name": "苏晚",
        "role": "女主角",
    })
    _write(root / "worldbook" / "wb_a.json", {
        "id": "wb_a", "project_id": pid,
        "title": "银河联邦", "category": "social_structure",
        "content": "人类星际政府",
    })
    _write(root / "project_memory" / f"{pid}.json", {
        "project_id": pid,
        "memories": [
            {"id": "mem_1", "content": "规则A", "category": "rule"},
            {"id": "mem_2", "content": "设定B", "category": "setting"},
        ],
    })
    _write(root / "storylines" / f"{pid}.json", {
        "project_id": pid,
        "nodes": [
            {"id": "n1", "title": "节点A", "chapter_num": 1, "x": 10, "y": 20},
            {"id": "n2", "title": "节点B", "chapter_num": 2},
        ],
        "edges": [
            {"id": "e1", "from": "n1", "to": "n2", "label": "导致"},
        ],
    })
    _write(root / "writing_knowledge" / "wk_a.json", {
        "id": "wk_a", "domain": "pacing", "title": "节奏",
        "content": "短句加快节奏",
    })
    _write(root / "chat_history" / f"{pid}_outline_chat.json", {
        "project_id": pid, "scope": "outline_chat",
        "messages": [
            {"role": "user", "content": "想法是..."},
            {"role": "assistant", "content": "建议是..."},
        ],
    })
    _write(root / "editor" / f"{pid}.json", {
        "project_id": pid,
        "volumes": [{
            "id": "vol1", "title": "第一卷", "order": 0,
            "chapters": [
                {"id": "ch_e1", "title": "第一章", "order": 0,
                 "content": "正文一", "outline": "大纲一",
                 "synopsis": "梗概一", "characters": ["李星河"]},
                {"id": "ch_e2", "title": "第二章", "order": 1,
                 "content": "正文二", "outline": "大纲二"},
            ],
        }],
    })
    _write(root / "calibration" / f"{pid}.json", {
        "project_id": pid, "style": "短句"
    })
    _write(root / f"reference_injection_{pid}.json", {
        "project_id": pid, "selections": ["ref1", "ref2"],
    })


class TestMigrate(unittest.TestCase):

    def setUp(self) -> None:
        self.tmpdir = Path(tempfile.mkdtemp())
        self.data_dir = self.tmpdir / "data"
        self.db_path = self.tmpdir / "novels.db"
        _make_data_tree(self.data_dir)

    def _run(self) -> dict:
        return run_migration(self.data_dir, self.db_path)

    def _con(self) -> sqlite3.Connection:
        c = sqlite3.connect(str(self.db_path))
        c.row_factory = sqlite3.Row
        return c

    def test_full_migration_row_counts(self) -> None:
        report = self._run()
        # Characters: 2 inserted, 0 skipped
        self.assertEqual(report["characters"]["inserted"], 2)
        # Worldbook: 1
        self.assertEqual(report["worldbook"]["inserted"], 1)
        # Project memory: 2 entries
        self.assertEqual(report["project_memory"]["inserted"], 2)
        # Storylines: 2 nodes + 1 edge = 3
        self.assertEqual(report["storylines"]["inserted"], 3)
        # writing_knowledge: table dropped in v3.1; migrator still
        # attempts the insert but it skips because the table is gone.
        self.assertEqual(report["writing_knowledge"]["inserted"], 0)
        self.assertEqual(report["writing_knowledge"]["skipped"], 1)
        # Chat: 2 messages
        self.assertEqual(report["chat_history"]["inserted"], 2)
        # Editor: 1 blob + 2 chapter rows = 3
        self.assertEqual(report["editor"]["inserted"], 3)
        # Project blobs: 1 calibration + 1 reference_injection
        self.assertEqual(report["project_blobs"]["inserted"], 2)

        # Verify a few rows landed in the right tables
        with self._con() as c:
            chars = c.execute("SELECT name FROM characters ORDER BY name").fetchall()
            self.assertEqual([r["name"] for r in chars], ["李星河", "苏晚"])

            ch = c.execute(
                "SELECT title, outline FROM chapters WHERE chapter_num=1"
            ).fetchone()
            self.assertEqual(ch["title"], "第一章")
            self.assertEqual(ch["outline"], "大纲一")

            blob = c.execute(
                "SELECT scope FROM project_blobs WHERE scope='editor'"
            ).fetchone()
            self.assertIsNotNone(blob)

            ref_blob = c.execute(
                "SELECT scope FROM project_blobs WHERE scope='reference_injection'"
            ).fetchone()
            self.assertIsNotNone(ref_blob)

    def test_idempotent_rerun_is_noop(self) -> None:
        first = self._run()
        second = self._run()
        # Total inserted on rerun should be small (only project_blobs use REPLACE
        # so they get re-inserted; counted tables use OR IGNORE).
        for name in ["characters", "worldbook", "project_memory",
                     "storylines", "chat_history"]:
            self.assertEqual(
                second[name]["inserted"], 0,
                f"second run inserted into {name}, expected idempotent",
            )

    def test_handles_missing_data_dir_gracefully(self) -> None:
        empty = self.tmpdir / "empty_data"
        empty.mkdir()
        # Should not crash with no JSON files present
        report = run_migration(empty, self.tmpdir / "empty.db")
        for v in report.values():
            self.assertEqual(v["inserted"], 0)


if __name__ == "__main__":
    unittest.main()
