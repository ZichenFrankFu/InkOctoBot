"""
Unit tests for the storyline / writing_knowledge / chat_history APIs
on ``ui/backend/app/services/project_store.py``.

Run against an isolated SQLite file with the v2 schema applied.
"""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from storage.project_schema import ensure_creation_tables
from ui.backend.app.services import project_store


def _fresh_db() -> str:
    db = os.path.join(tempfile.mkdtemp(), "store_pt2.db")
    con = sqlite3.connect(db)
    con.execute("PRAGMA foreign_keys=ON")
    ensure_creation_tables(con)
    con.execute("INSERT INTO projects (project_id, title) VALUES (?, ?)",
                ("p1", "test"))
    con.commit()
    con.close()
    return db


class TestStoryline(unittest.TestCase):

    def setUp(self) -> None:
        self.db = _fresh_db()

    def test_empty(self) -> None:
        g = project_store.get_storyline(self.db, "p1")
        self.assertEqual(g["nodes"], [])
        self.assertEqual(g["edges"], [])

    def test_replace_round_trip(self) -> None:
        project_store.replace_storyline(self.db, "p1",
            nodes=[
                {"id": "n1", "title": "起", "chapter_num": 1,
                 "summary": "...", "x": 10, "y": 20,
                 "time": "春", "location": "村口", "color": "blue"},
                {"id": "n2", "title": "承", "chapter_num": 2,
                 "x": 100, "y": 20},
            ],
            edges=[
                {"id": "e1", "from": "n1", "to": "n2", "label": "导致"},
            ],
        )
        g = project_store.get_storyline(self.db, "p1")
        self.assertEqual(len(g["nodes"]), 2)
        self.assertEqual(len(g["edges"]), 1)
        # node round-trip preserves extra keys (color)
        n1 = next(n for n in g["nodes"] if n["id"] == "n1")
        self.assertEqual(n1["title"], "起")
        self.assertEqual(n1["x"], 10)
        self.assertEqual(n1["color"], "blue")
        # edge maps fields correctly
        e1 = g["edges"][0]
        self.assertEqual(e1["from"], "n1")
        self.assertEqual(e1["to"], "n2")
        self.assertEqual(e1["label"], "导致")

    def test_replace_is_destructive(self) -> None:
        project_store.replace_storyline(self.db, "p1",
            nodes=[{"id": "n_old", "title": "old"}], edges=[])
        project_store.replace_storyline(self.db, "p1",
            nodes=[{"id": "n_new", "title": "new"}], edges=[])
        g = project_store.get_storyline(self.db, "p1")
        self.assertEqual([n["id"] for n in g["nodes"]], ["n_new"])

    def test_edge_without_endpoints_is_skipped(self) -> None:
        project_store.replace_storyline(self.db, "p1",
            nodes=[{"id": "n1"}],
            edges=[
                {"id": "e_bad", "from": "n1"},
                {"id": "e_ok", "from": "n1", "to": "n1"},
            ],
        )
        g = project_store.get_storyline(self.db, "p1")
        self.assertEqual([e["id"] for e in g["edges"]], ["e_ok"])


# TestWritingKnowledge removed in v3.1 — the writing_knowledge table,
# helpers, prompt loader, API endpoints and UI pages were all dropped.


class TestChatHistory(unittest.TestCase):

    def setUp(self) -> None:
        self.db = _fresh_db()

    def test_save_and_load(self) -> None:
        project_store.replace_chat_history(self.db, "p1", "pipeline", [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello", "proactive": True},
        ])
        msgs = project_store.get_chat_history(self.db, "p1", "pipeline")
        self.assertEqual(len(msgs), 2)
        self.assertEqual(msgs[0]["role"], "user")
        self.assertEqual(msgs[1]["content"], "hello")
        # meta field round-trips
        self.assertTrue(msgs[1].get("proactive"))

    def test_scopes_isolated(self) -> None:
        project_store.replace_chat_history(self.db, "p1", "pipeline",
            [{"role": "user", "content": "A"}])
        project_store.replace_chat_history(self.db, "p1", "outline_chat",
            [{"role": "user", "content": "B"}])
        self.assertEqual(
            project_store.get_chat_history(self.db, "p1", "pipeline")[0]["content"],
            "A",
        )
        self.assertEqual(
            project_store.get_chat_history(self.db, "p1", "outline_chat")[0]["content"],
            "B",
        )

    def test_clear(self) -> None:
        project_store.replace_chat_history(self.db, "p1", "pipeline",
            [{"role": "user", "content": "x"}])
        project_store.clear_chat_history(self.db, "p1", "pipeline")
        self.assertEqual(
            project_store.get_chat_history(self.db, "p1", "pipeline"), [],
        )

    def test_unknown_role_coerced(self) -> None:
        project_store.replace_chat_history(self.db, "p1", "pipeline",
            [{"role": "weird_role", "content": "x"}])
        msgs = project_store.get_chat_history(self.db, "p1", "pipeline")
        self.assertEqual(msgs[0]["role"], "user")


if __name__ == "__main__":
    unittest.main()
