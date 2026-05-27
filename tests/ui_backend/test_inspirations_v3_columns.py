"""Tests for inspirations v3 ALTERs + IdeaDB extensions (LOADER_SPEC Loader 4)."""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from knowledge.idea_db import (
    IdeaDB, inspiration_embedding_text, hash_embedding_text,
)
from storage.idea_schema import (
    ensure_idea_tables, _ensure_inspirations_v3_columns,
)


def _fresh_idea_db() -> tuple[IdeaDB, str]:
    path = os.path.join(tempfile.mkdtemp(), "idea.db")
    return IdeaDB(path), path


class TestSchema(unittest.TestCase):

    def test_fresh_db_has_v3_columns(self) -> None:
        _, path = _fresh_idea_db()
        with sqlite3.connect(path) as c:
            cols = {r[1] for r in c.execute("PRAGMA table_info(inspirations)")}
        for col in (
            "project_id", "tags_json", "embedding_json",
            "embedding_text_hash", "used_in_chapters_json",
        ):
            self.assertIn(col, cols)

    def test_alter_upgrades_v1_table(self) -> None:
        """A legacy v1 idea.db gets the new columns added without losing rows."""
        path = os.path.join(tempfile.mkdtemp(), "v1.db")
        with sqlite3.connect(path) as c:
            c.execute(
                "CREATE TABLE inspirations ("
                "id TEXT PRIMARY KEY, category TEXT, title TEXT, content TEXT, "
                "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, "
                "updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
            )
            c.execute(
                "INSERT INTO inspirations(id, category, title, content) "
                "VALUES ('old1','场景','旧标题','旧内容')"
            )
            c.commit()
            _ensure_inspirations_v3_columns(c)
            cols = {r[1] for r in c.execute("PRAGMA table_info(inspirations)")}
            row = c.execute(
                "SELECT id, project_id, content FROM inspirations WHERE id='old1'"
            ).fetchone()
        self.assertIn("project_id", cols)
        self.assertIn("tags_json", cols)
        self.assertEqual(row[0], "old1")
        self.assertIsNone(row[1])         # legacy row is "global"
        self.assertEqual(row[2], "旧内容")

    def test_alter_is_idempotent(self) -> None:
        _, path = _fresh_idea_db()
        with sqlite3.connect(path) as c:
            _ensure_inspirations_v3_columns(c)
            _ensure_inspirations_v3_columns(c)  # must not raise


class TestIdeaDBProjectScope(unittest.TestCase):

    def test_global_row_is_visible_to_every_project(self) -> None:
        db, _ = _fresh_idea_db()
        g = db.create_inspiration("其他", "全局", "全局内容")
        db.create_inspiration("场景", "私有A", "A 内容", project_id="pA")
        db.create_inspiration("场景", "私有B", "B 内容", project_id="pB")
        a_view = db.list_inspirations(project_id="pA")
        names = {r["title"] for r in a_view}
        self.assertEqual(names, {"全局", "私有A"})

    def test_no_project_filter_returns_everything(self) -> None:
        db, _ = _fresh_idea_db()
        db.create_inspiration("a", "x", "x", project_id="pA")
        db.create_inspiration("a", "y", "y", project_id="pB")
        self.assertEqual(len(db.list_inspirations()), 2)

    def test_tags_round_trip(self) -> None:
        db, _ = _fresh_idea_db()
        r = db.create_inspiration("a", "t", "c", tags=["场景", "氛围"])
        got = db.get_inspiration(r["id"])
        self.assertEqual(got["tags"], ["场景", "氛围"])


class TestEmbeddingLifecycle(unittest.TestCase):

    def test_canonical_text_includes_title_tags_content(self) -> None:
        text = inspiration_embedding_text({
            "title": "T", "content": "C", "tags": ["a", "b"],
        })
        self.assertIn("T", text)
        self.assertIn("a、b", text)
        self.assertIn("C", text)

    def test_set_and_invalidate_on_content_change(self) -> None:
        db, _ = _fresh_idea_db()
        r = db.create_inspiration("a", "t1", "原内容")
        db.set_embedding(r["id"], [0.1, 0.2, 0.3], "h1")
        emb = db.get_inspiration(r["id"], include_embedding=True)
        self.assertEqual(emb["embedding"], [0.1, 0.2, 0.3])
        self.assertEqual(emb["embedding_text_hash"], "h1")

        # Content change → embedding cleared, hash refreshed.
        db.update_inspiration(r["id"], content="新内容")
        emb = db.get_inspiration(r["id"], include_embedding=True)
        self.assertEqual(emb["embedding"], [])
        self.assertNotEqual(emb["embedding_text_hash"], "h1")
        self.assertNotEqual(emb["embedding_text_hash"], "")

    def test_metadata_only_update_preserves_embedding(self) -> None:
        """No-op canonical text → keep the embedding."""
        db, _ = _fresh_idea_db()
        r = db.create_inspiration("a", "t", "c")
        # Use the canonical hash so a same-text update doesn't trip the
        # invalidate path.
        text = inspiration_embedding_text(r)
        canonical = hash_embedding_text(text)
        db.set_embedding(r["id"], [0.5, 0.6], canonical)
        # category change doesn't touch the canonical text (title+tags+content).
        db.update_inspiration(r["id"], category="新分类")
        emb = db.get_inspiration(r["id"], include_embedding=True)
        self.assertEqual(emb["embedding"], [0.5, 0.6])
        self.assertEqual(emb["embedding_text_hash"], canonical)


class TestUsedInChapters(unittest.TestCase):

    def test_appends_and_allows_duplicates(self) -> None:
        db, _ = _fresh_idea_db()
        r = db.create_inspiration("a", "t", "c")
        db.mark_used_in_chapter(r["id"], 5)
        db.mark_used_in_chapter(r["id"], 5)
        db.mark_used_in_chapter(r["id"], 12)
        self.assertEqual(
            db.get_inspiration(r["id"])["used_in_chapters"],
            [5, 5, 12],
        )

    def test_unknown_id_is_no_op(self) -> None:
        db, _ = _fresh_idea_db()
        # Must not raise.
        db.mark_used_in_chapter("nope", 1)


if __name__ == "__main__":
    unittest.main()
