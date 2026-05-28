"""Tests for ChapterContext (roadmap Stage 2 Tasks 2.1-2.3)."""
from __future__ import annotations

import asyncio
import os
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from storage.project_schema import ensure_creation_tables
from ui.backend.app.services.chapter_context import (
    BuildRequest,
    ChapterContext,
    CommitBlocked,
)


def _fresh_db() -> str:
    db = os.path.join(tempfile.mkdtemp(), "cc.db")
    con = sqlite3.connect(db)
    ensure_creation_tables(con)
    con.execute(
        "INSERT INTO projects (project_id, title) VALUES ('p1', 'cc-test')"
    )
    con.execute(
        "INSERT INTO chapters (chapter_id, project_id, chapter_num) "
        "VALUES ('ch3', 'p1', 3)"
    )
    con.commit()
    con.close()
    return db


# ─────────── BuildRequest ───────────


class TestBuildRequest(unittest.TestCase):

    def test_accepts_extra_fields_silently(self) -> None:
        """``extra='ignore'`` lets handlers pass req.model_dump()
        without surgery — irrelevant fields are dropped."""
        req = BuildRequest(
            project_id="p1", chapter_id="ch1",
            unknown_extra="should be ignored",
        )
        self.assertEqual(req.project_id, "p1")
        self.assertFalse(hasattr(req, "unknown_extra"))

    def test_defaults_match_handler_expectations(self) -> None:
        req = BuildRequest(project_id="p", chapter_id="c")
        self.assertEqual(req.chapter_num, 1)
        self.assertEqual(req.generation_mode, "fresh")
        self.assertEqual(req.characters, [])


# ─────────── build() ───────────


class TestBuild(unittest.IsolatedAsyncioTestCase):

    def setUp(self) -> None:
        self.db = _fresh_db()

    async def test_build_populates_all_fields(self) -> None:
        req = BuildRequest(
            project_id="p1", chapter_id="ch3", chapter_num=3,
            characters=["主角"],
            user_special_requirements="本章需有打斗场面",
        )
        with mock.patch(
            "ui.backend.app.services.project_paths.get_db_path",
            return_value=self.db,
        ):
            ctx = await ChapterContext.build(req)
        self.assertEqual(ctx.project_id, "p1")
        self.assertEqual(ctx.chapter_id, "ch3")
        self.assertEqual(ctx.chapter_num, 3)
        self.assertEqual(ctx.characters, ["主角"])
        self.assertEqual(ctx.user_special_requirements, "本章需有打斗场面")
        # Loader blocks + sections + diagnostics dict shape — even an
        # empty project should produce a well-formed structure.
        self.assertIsInstance(ctx.loader_blocks, dict)
        self.assertIsInstance(ctx.sections, list)
        self.assertIsInstance(ctx.diagnostics, dict)
        # Truth bundle obj is captured for typed access; the dict
        # mirror has the 6 expected keys.
        self.assertIsNotNone(ctx.truth_bundle_obj)
        for k in ("bundle_text", "pressured_hooks_text", "ledger_anchors"):
            self.assertIn(k, ctx.truth_bundle)

    async def test_build_accepts_dict_too(self) -> None:
        """Convenience: handlers can pass req.model_dump() directly."""
        with mock.patch(
            "ui.backend.app.services.project_paths.get_db_path",
            return_value=self.db,
        ):
            ctx = await ChapterContext.build({
                "project_id": "p1", "chapter_id": "ch3", "chapter_num": 3,
            })
        self.assertEqual(ctx.chapter_id, "ch3")

    async def test_build_degrades_on_missing_db(self) -> None:
        """No db_path → loader blocks present but empty strings + empty
        truth_bundle, still a valid ChapterContext. (The 14 loaders
        each return their block_id with an empty value when there's
        no data — they don't disappear from the result dict.)"""
        with mock.patch(
            "ui.backend.app.services.project_paths.get_db_path",
            side_effect=RuntimeError("no db"),
        ):
            ctx = await ChapterContext.build({
                "project_id": "p1", "chapter_id": "ch_does_not_exist",
            })
        # Block keys present; all values empty strings.
        self.assertGreater(len(ctx.loader_blocks), 0)
        for v in ctx.loader_blocks.values():
            self.assertEqual(v, "")
        # Truth bundle present but data-driven fields empty.
        # (bundle_text contains YAML scaffolding even on empty tables
        # — that's a property of build_state_context, not ChapterContext.)
        self.assertEqual(ctx.truth_bundle.get("pressured_hooks_text"), "")
        self.assertEqual(ctx.truth_bundle.get("ledger_anchors"), [])


# ─────────── commit() ───────────


class TestCommit(unittest.IsolatedAsyncioTestCase):

    def setUp(self) -> None:
        self.db = _fresh_db()

    async def test_commit_writes_text_versions_and_fires_pipeline(self) -> None:
        ctx = ChapterContext(
            project_id="p1", chapter_id="ch3", chapter_num=3,
            db_path=self.db,
        )
        # Stub the LLM-using sub-tasks so the pipeline reaches terminal
        # state quickly.
        from ui.backend.app.services.commit_pipeline.sub_tasks import (
            summarizer as summarizer_mod,
            event_extractor as event_extractor_mod,
            state_extractor as state_extractor_mod,
        )

        async def _summary(*a, **kw): return ("摘要", "正文", "mock/m")
        async def _events(*a, **kw): return ([], "mock/m")
        async def _state(*a, **kw):
            return ({"state_deltas": [], "ledger_deltas": [],
                      "emotion_deltas": [], "new_entities": []}, "mock/m")

        with mock.patch.object(summarizer_mod, "_call_llm", _summary), \
             mock.patch.object(event_extractor_mod, "_call_llm", _events), \
             mock.patch.object(state_extractor_mod, "_call_llm", _state), \
             mock.patch(
                 "ui.backend.app.services.project_paths.get_db_path",
                 return_value=self.db,
             ):
            version_id = await ctx.commit(
                "第三章正文。" * 30, model_used="multi_agent.test",
            )

        self.assertTrue(version_id)
        # Verify text_versions row exists.
        with sqlite3.connect(self.db) as c:
            row = c.execute(
                "SELECT source, model_used, content FROM text_versions "
                "WHERE chapter_id = ?", ("ch3",),
            ).fetchone()
        self.assertEqual(row[0], "ai")
        self.assertEqual(row[1], "multi_agent.test")
        self.assertGreater(len(row[2]), 50)

        # Poll for commit_tasks completion.
        for _ in range(100):
            with sqlite3.connect(self.db) as c:
                n_done = c.execute(
                    "SELECT COUNT(*) FROM commit_tasks "
                    "WHERE state IN ('completed','failed_needs_manual','skipped')"
                ).fetchone()[0]
                n_total = c.execute(
                    "SELECT COUNT(*) FROM commit_tasks"
                ).fetchone()[0]
            if n_total > 0 and n_done == n_total:
                break
            await asyncio.sleep(0.05)
        self.assertGreater(n_total, 0)

    async def test_commit_skips_without_ids(self) -> None:
        ctx = ChapterContext(
            project_id="", chapter_id="", db_path=self.db,
        )
        version_id = await ctx.commit("text")
        self.assertEqual(version_id, "")

    async def test_commit_encodes_audit_status_into_model_used(self) -> None:
        from ui.backend.app.services.truth_files import AuditResult
        ctx = ChapterContext(
            project_id="p1", chapter_id="ch3", chapter_num=3,
            db_path=self.db,
        )
        audit = AuditResult(
            chapter_id="ch3", chapter_num=3,
            success=True, audit_status="audit_passed",
        )
        with mock.patch(
            "ui.backend.app.services.commit_pipeline.fire_and_forget_from_handler",
        ):
            await ctx.commit("text", audit_result=audit)
        with sqlite3.connect(self.db) as c:
            row = c.execute(
                "SELECT model_used FROM text_versions WHERE chapter_id = ?",
                ("ch3",),
            ).fetchone()
        self.assertEqual(row[0], "chapter_context.audit_passed")

    async def test_commit_blocked_raises_when_audit_failed_and_required(self) -> None:
        from ui.backend.app.services.truth_files import AuditResult
        ctx = ChapterContext(
            project_id="p1", chapter_id="ch3", chapter_num=3,
            db_path=self.db,
        )
        audit = AuditResult(
            chapter_id="ch3", chapter_num=3,
            success=False, audit_status="audit_failed",
            issues=[{"rule_id": "x", "severity": "error"}],
        )
        with self.assertRaises(CommitBlocked) as cm:
            await ctx.commit(
                "text", audit_result=audit, require_audit_passed=True,
            )
        self.assertEqual(cm.exception.audit_status, "audit_failed")
        # Nothing written.
        with sqlite3.connect(self.db) as c:
            n = c.execute(
                "SELECT COUNT(*) FROM text_versions WHERE chapter_id = ?",
                ("ch3",),
            ).fetchone()[0]
        self.assertEqual(n, 0)

    async def test_commit_does_not_fire_post_commit_when_disabled(self) -> None:
        ctx = ChapterContext(
            project_id="p1", chapter_id="ch3", chapter_num=3,
            db_path=self.db,
        )
        fired = {"n": 0}

        def fake_fire(**kw):
            fired["n"] += 1

        with mock.patch(
            "ui.backend.app.services.commit_pipeline.fire_and_forget_from_handler",
            fake_fire,
        ):
            await ctx.commit("text", trigger_post_commit=False)
        self.assertEqual(fired["n"], 0)
        # text_versions still written.
        with sqlite3.connect(self.db) as c:
            n = c.execute(
                "SELECT COUNT(*) FROM text_versions WHERE chapter_id = 'ch3'"
            ).fetchone()[0]
        self.assertEqual(n, 1)


# ─────────── world_rules_legacy_text helper ───────────


class TestWorldRulesLegacyText(unittest.TestCase):

    def test_assembles_loader_blocks_and_bundle(self) -> None:
        ctx = ChapterContext(
            project_id="p1", chapter_id="ch3",
            loader_blocks={
                "character_cards": "## 角色卡\n主角：林天",
                "worldbook":       "## 世界书\n灵气复苏",
                "irrelevant":      "should not be included",
            },
            truth_bundle={
                "bundle_text":          "## Truth bundle\n主角位于幽冥谷",
                "pressured_hooks_text": "• 揭示主角身世",
                "ledger_anchors":       [],
            },
        )
        text = ctx.world_rules_legacy_text()
        self.assertIn("林天", text)
        self.assertIn("灵气复苏", text)
        self.assertIn("幽冥谷", text)
        self.assertIn("揭示主角身世", text)
        self.assertNotIn("should not be included", text)


if __name__ == "__main__":
    unittest.main()
