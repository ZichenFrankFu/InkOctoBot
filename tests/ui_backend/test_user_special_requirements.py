"""Tests for the user_special_requirements loader (LOADER_SPEC v3.1)."""
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
from ui.backend.app.services import project_store
from ui.backend.app.services.prompt_context.loaders import (
    user_special_requirements as usr,
)


def _fresh_db() -> str:
    db = os.path.join(tempfile.mkdtemp(), "usr.db")
    con = sqlite3.connect(db)
    ensure_creation_tables(con)
    con.execute("INSERT INTO projects(project_id, title) VALUES('p1','t')")
    con.commit()
    con.close()
    return db


class _Env:
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


class TestSchemaColumn(unittest.TestCase):

    def test_column_exists(self) -> None:
        db = _fresh_db()
        with sqlite3.connect(db) as c:
            cols = {r[1] for r in c.execute("PRAGMA table_info(chapters)")}
        self.assertIn("special_requirements", cols)


class TestSaveEditorDocWrites(unittest.TestCase):

    def test_save_editor_doc_persists_field(self) -> None:
        db = _fresh_db()
        project_store.save_editor_doc(db, "p1", {
            "volumes": [{"chapters": [{
                "id": "ch1", "chapter_id": "ch1",
                "synopsis": "本章",
                "special_requirements": "本章请用慢节奏；保留所有铺垫",
            }]}],
        })
        with sqlite3.connect(db) as c:
            row = c.execute(
                "SELECT special_requirements FROM chapters WHERE chapter_id='ch1'"
            ).fetchone()
        self.assertEqual(row[0], "本章请用慢节奏；保留所有铺垫")


class TestLoader(unittest.TestCase):

    def setUp(self) -> None:
        self.db = _fresh_db()

    def test_empty_returns_none(self) -> None:
        project_store.save_editor_doc(self.db, "p1", {
            "volumes": [{"chapters": [{
                "id": "ch1", "chapter_id": "ch1", "synopsis": "x",
            }]}],
        })
        with _Env(self.db):
            self.assertIsNone(usr.plan("p1", "ch1"))
            self.assertEqual(usr.load("p1", "ch1"), "")

    def test_no_chapter_id_returns_none(self) -> None:
        with _Env(self.db):
            self.assertIsNone(usr.plan("p1", ""))

    def test_renders_when_text_present(self) -> None:
        project_store.save_editor_doc(self.db, "p1", {
            "volumes": [{"chapters": [{
                "id": "ch1", "chapter_id": "ch1",
                "synopsis": "x",
                "special_requirements": "慢节奏，注重内心戏",
            }]}],
        })
        with _Env(self.db):
            p = usr.plan("p1", "ch1")
            self.assertIsNotNone(p)
            self.assertGreater(p.natural_length, 0)
            out = usr.load("p1", "ch1")
        self.assertIn("用户特别要求", out)
        self.assertIn("慢节奏", out)

    def test_render_respects_budget(self) -> None:
        long_text = "长长的额外要求 " * 200  # ~2400 chars
        project_store.save_editor_doc(self.db, "p1", {
            "volumes": [{"chapters": [{
                "id": "ch1", "chapter_id": "ch1",
                "synopsis": "x",
                "special_requirements": long_text,
            }]}],
        })
        with _Env(self.db):
            p = usr.plan("p1", "ch1")
            short = p.render(200)
            full = p.render(p.maximum)
        self.assertLess(len(short), len(full))


if __name__ == "__main__":
    unittest.main()
