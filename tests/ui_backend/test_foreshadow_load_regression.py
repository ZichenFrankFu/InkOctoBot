"""
Regression: _load_unresolved_foreshadowing in generation_api previously
called EpisodicTimeline.get_unresolved_foreshadowing, which was removed
in commit 10337fc (v2.1 dedup). Calling it crashed at runtime.

This test calls the helper directly with a seeded pending_hooks row and
asserts it returns formatted output (not an empty string from the
``except: return ''`` swallow path).
"""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from storage.project_schema import ensure_creation_tables


def _seed_db_with_hook() -> str:
    db = os.path.join(tempfile.mkdtemp(), "fore.db")
    con = sqlite3.connect(db)
    con.execute("PRAGMA foreign_keys=ON")
    ensure_creation_tables(con)
    con.execute("INSERT INTO projects (project_id, title) VALUES ('p1', 't')")
    con.execute(
        """INSERT INTO pending_hooks
           (hook_id, project_id, description, status, importance,
            origin_chapter)
           VALUES ('h1', 'p1', '黑衣人留下的玉佩', 'open', 'A', 3)"""
    )
    con.commit()
    con.close()
    return db


class TestLoadUnresolvedForeshadowing(unittest.TestCase):

    def test_reads_from_pending_hooks_not_episodic(self) -> None:
        """The v2.1 path goes through MemoryManager → pending_hooks.
        Previously this crashed with AttributeError because
        EpisodicTimeline no longer has get_unresolved_foreshadowing.
        """
        db = _seed_db_with_hook()
        from ui.backend.app.routers.generation_api import _load_unresolved_foreshadowing
        out = _load_unresolved_foreshadowing("p1", db, chapter_num=10)
        self.assertIn("黑衣人留下的玉佩", out)
        # gap = 10 - 3 = 7, so it lands in 'upcoming' bucket
        self.assertIn("距今", out)

    def test_empty_returns_empty_string(self) -> None:
        db = os.path.join(tempfile.mkdtemp(), "empty.db")
        with sqlite3.connect(db) as c:
            ensure_creation_tables(c)
            c.execute("INSERT INTO projects (project_id, title) VALUES ('p1', 't')")
            c.commit()
        from ui.backend.app.routers.generation_api import _load_unresolved_foreshadowing
        self.assertEqual(
            _load_unresolved_foreshadowing("p1", db, chapter_num=1),
            "",
        )


if __name__ == "__main__":
    unittest.main()
