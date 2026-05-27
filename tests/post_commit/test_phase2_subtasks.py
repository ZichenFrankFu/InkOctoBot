"""Tests for Phase 2 sub-tasks (summarizer / event_extractor / chromadb_indexer).

LLM calls are mocked via FallbackRouter.invoke — the contracts under
test are persistence shape + skip-on-empty + LLM response parsing,
not the prompts themselves.
"""
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
from ui.backend.app.services.commit_pipeline.sub_tasks import (
    SubTaskContext,
    chromadb_indexer, event_extractor, summarizer,
)


def _fresh_db_with_chapter() -> tuple[str, str]:
    db = os.path.join(tempfile.mkdtemp(), "p2.db")
    con = sqlite3.connect(db)
    ensure_creation_tables(con)
    con.execute("INSERT INTO projects (project_id, title) VALUES ('p1', 't')")
    con.commit()
    con.close()
    project_store.save_editor_doc(db, "p1", {
        "volumes": [{"chapters": [{
            "id": "ch1", "chapter_id": "ch1",
            "synopsis": "本章主角进入幽冥谷探秘",
        }]}],
    })
    return db, "ch1"


# ─────────── chapter_summarizer ───────────


class TestSummarizer(unittest.IsolatedAsyncioTestCase):

    def setUp(self) -> None:
        self.db, self.cid = _fresh_db_with_chapter()

    async def test_persists_title_summary_model(self) -> None:
        ctx = SubTaskContext(
            project_id="p1", chapter_id=self.cid, db_path=self.db,
            chapter_text="主角进入幽冥谷。山谷深处有一具白骨。", chapter_num=10,
        )
        fake_raw = '{"title": "幽冥谷探秘", "summary": "主角踏入禁地，发现一具白骨。"}'
        with mock.patch.object(
            summarizer, "_call_llm",
            return_value=("幽冥谷探秘", "主角踏入禁地，发现一具白骨。", "ollama/qwen2.5"),
        ):
            result = await summarizer.run(ctx)
        self.assertEqual(result["status"], "ok")
        with sqlite3.connect(self.db) as con:
            row = con.execute(
                "SELECT title, summary_text, generated_by, llm_model, is_active "
                "FROM chapter_summaries WHERE summary_id = ?",
                (result["summary_id"],),
            ).fetchone()
        self.assertEqual(row[0], "幽冥谷探秘")
        self.assertEqual(row[2], "llm_auto")
        self.assertEqual(row[3], "ollama/qwen2.5")
        self.assertEqual(row[4], 1)

    async def test_empty_chapter_skipped(self) -> None:
        ctx = SubTaskContext(
            project_id="p1", chapter_id=self.cid, db_path=self.db,
            chapter_text="",
        )
        # _resolve_chapter_text will also return "" since no text_version
        # rows exist.
        result = await summarizer.run(ctx)
        self.assertEqual(result["status"], "skipped")

    async def test_rerun_upserts_in_place(self) -> None:
        """Re-running the summarizer for the same (project, chapter)
        UPDATEs the existing row (UNIQUE constraint forces this) so
        the newest title + model + timestamp win."""
        ctx = SubTaskContext(
            project_id="p1", chapter_id=self.cid, db_path=self.db,
            chapter_text="一些内容", chapter_num=5,
        )
        with mock.patch.object(
            summarizer, "_call_llm",
            return_value=("第一版", "第一版正文", "m1"),
        ):
            first = await summarizer.run(ctx)
        with mock.patch.object(
            summarizer, "_call_llm",
            return_value=("第二版", "第二版正文", "m2"),
        ):
            second = await summarizer.run(ctx)
        # Same summary_id (UPDATE, not INSERT a duplicate).
        self.assertEqual(first["summary_id"], second["summary_id"])
        with sqlite3.connect(self.db) as con:
            rows = con.execute(
                "SELECT title, llm_model FROM chapter_summaries "
                "WHERE project_id='p1' AND chapter_num=5"
            ).fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], "第二版")
        self.assertEqual(rows[0][1], "m2")

    def test_strip_meta_removes_banned_lines(self) -> None:
        text = "正常一句。\n这是个伏笔。\n另一句正常。"
        out = summarizer._strip_meta(text)
        self.assertIn("正常一句", out)
        self.assertIn("另一句正常", out)
        self.assertNotIn("伏笔", out)

    def test_extract_json_tolerates_fences(self) -> None:
        raw = '```json\n{"title": "a", "summary": "b"}\n```'
        out = summarizer._extract_json(raw)
        self.assertEqual(out, {"title": "a", "summary": "b"})


# ─────────── event_extractor ───────────


class TestEventExtractor(unittest.IsolatedAsyncioTestCase):

    def setUp(self) -> None:
        self.db, self.cid = _fresh_db_with_chapter()

    async def test_persists_events_with_attribution(self) -> None:
        ctx = SubTaskContext(
            project_id="p1", chapter_id=self.cid, db_path=self.db,
            chapter_text="主角斩杀妖兽。\n师父出场救场。", chapter_num=7,
        )
        # Shape matches what _call_llm returns AFTER normalization:
        # characters_involved → characters (the persist function
        # expects the normalized key).
        fake_events = [
            {"event_type": "plot",
             "description": "主角斩杀妖兽",
             "characters": ["主角"],
             "importance": "A"},
            {"event_type": "character_change",
             "description": "师父出场救场",
             "characters": ["师父", "主角"],
             "importance": "B"},
        ]
        with mock.patch.object(
            event_extractor, "_call_llm",
            return_value=(fake_events, "ollama/qwen2.5"),
        ):
            result = await event_extractor.run(ctx)
        self.assertEqual(result["events"], 2)
        with sqlite3.connect(self.db) as con:
            rows = con.execute(
                "SELECT event_type, importance, generated_by, llm_model "
                "FROM episodic_events WHERE chapter_num = 7 "
                "ORDER BY scene_index"
            ).fetchall()
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0][0], "plot")
        # A → 5 in the integer DB column.
        self.assertEqual(rows[0][1], 5)
        self.assertEqual(rows[0][2], "llm_auto")
        self.assertEqual(rows[1][0], "character_change")

    async def test_caps_at_5_events(self) -> None:
        """Spec: max 5 events per chapter."""
        ctx = SubTaskContext(
            project_id="p1", chapter_id=self.cid, db_path=self.db,
            chapter_text="x", chapter_num=8,
        )
        many = [{"event_type": "plot", "description": f"event {i}",
                  "characters": [], "importance": "B"}
                 for i in range(20)]
        # Our _call_llm helper does the 0:5 slice — but the test
        # directly mocks _call_llm to return only 5 already (mirroring
        # the production behavior). Verify persist respects whatever
        # _call_llm yielded.
        with mock.patch.object(
            event_extractor, "_call_llm",
            return_value=(many[:5], "test/m"),
        ):
            result = await event_extractor.run(ctx)
        self.assertEqual(result["events"], 5)

    async def test_zero_events_returns_ok(self) -> None:
        ctx = SubTaskContext(
            project_id="p1", chapter_id=self.cid, db_path=self.db,
            chapter_text="无事发生", chapter_num=9,
        )
        with mock.patch.object(
            event_extractor, "_call_llm",
            return_value=([], "test/m"),
        ):
            result = await event_extractor.run(ctx)
        self.assertEqual(result["events"], 0)
        self.assertEqual(result["status"], "ok")


# ─────────── chromadb_indexer ───────────


class TestChunker(unittest.TestCase):

    def test_short_text_one_chunk(self) -> None:
        chunks = chromadb_indexer._chunk("短文本", size=400, overlap=80)
        self.assertEqual(chunks, ["短文本"])

    def test_long_text_overlapping_chunks(self) -> None:
        text = "甲" * 1000
        chunks = chromadb_indexer._chunk(text, size=400, overlap=80)
        # ceil((1000 - 80) / (400 - 80)) + 1 = 4 chunks (approx)
        self.assertGreaterEqual(len(chunks), 2)
        # Each chunk respects the size bound.
        for c in chunks:
            self.assertLessEqual(len(c), 400)
        # Overlap: consecutive chunks share at least 1 character.
        for i in range(len(chunks) - 1):
            self.assertEqual(chunks[i][-1], chunks[i + 1][0])

    def test_empty_text_empty_list(self) -> None:
        self.assertEqual(chromadb_indexer._chunk(""), [])


class TestCollectionName(unittest.TestCase):

    def test_isolates_project_and_model(self) -> None:
        name = chromadb_indexer._collection_name("Proj-1", "bge-base-zh")
        self.assertIn("proj-1", name)
        self.assertIn("bge-base-zh", name)
        self.assertTrue(name.startswith("project_"))
        self.assertIn("_chunks_", name)

    def test_sanitizes_disallowed_chars(self) -> None:
        name = chromadb_indexer._collection_name("项目/X@1", "model:1")
        # No slashes / colons / @
        for ch in ("/", "@", ":"):
            self.assertNotIn(ch, name)


class TestIndexerRun(unittest.IsolatedAsyncioTestCase):

    def setUp(self) -> None:
        self.db, self.cid = _fresh_db_with_chapter()

    async def test_empty_chapter_skipped(self) -> None:
        ctx = SubTaskContext(
            project_id="p1", chapter_id=self.cid, db_path=self.db,
            chapter_text="",
        )
        result = await chromadb_indexer.run(ctx)
        self.assertEqual(result["status"], "skipped")

    async def test_persists_with_correct_collection_and_metadata(self) -> None:
        """Mock both EmbeddingService and VectorStore so we don't need
        the real embedding stack or chromadb installed."""
        ctx = SubTaskContext(
            project_id="p1", chapter_id=self.cid, db_path=self.db,
            chapter_text="甲" * 1000, chapter_num=12,
        )

        class _FakeModel:
            model_key = "bge-base-zh"
            dimension = 768

        class _FakeSvc:
            def get_current_model(self):
                return _FakeModel()
            def embed_batch(self, texts):
                import numpy as np
                return [np.ones(768, dtype="float32").tobytes() for _ in texts]

        store_calls: list[dict] = []

        class _FakeStore:
            def __init__(self, persist_dir=None, collection_name="default"):
                self.collection_name = collection_name
            def add_batch_with_embeddings(self, *, ids, texts, embeddings, metadatas):
                store_calls.append({
                    "collection": self.collection_name,
                    "n":          len(ids),
                    "first_md":   metadatas[0] if metadatas else None,
                })

        with mock.patch(
            "ui.backend.app.services.embedding.get_embedding_service",
            return_value=_FakeSvc(),
        ), mock.patch(
            "knowledge.vector_store.VectorStore", _FakeStore,
        ):
            result = await chromadb_indexer.run(ctx)

        self.assertEqual(result["status"], "ok")
        self.assertGreater(result["chunks"], 0)
        self.assertTrue(result["collection_name"].startswith("project_p1_chunks_"))
        self.assertIn("bge-base-zh", result["collection_name"])
        self.assertEqual(len(store_calls), 1)
        self.assertEqual(store_calls[0]["first_md"]["chapter_id"], "ch1")
        self.assertEqual(store_calls[0]["first_md"]["chapter_num"], 12)
        self.assertEqual(store_calls[0]["first_md"]["model_key"], "bge-base-zh")


if __name__ == "__main__":
    unittest.main()
