"""Tests for the TruthFilesIntegration class (roadmap Stage 1 Task 2).

The class is a thin wrapper over the existing
``agents/production/storyland_state_integration`` helpers, keyed on
``chapter_id`` instead of ``(project_id, chapter_num)``. Tests focus
on:

  - key resolution: chapter_id → (project_id, chapter_num) lookup
  - build_bundle: degrades gracefully when truth tables are empty
  - apply_settlement: wraps the raw dict into typed AuditResult +
    persists audit_status to chapters table
  - check_audit_gate: mirrors project_store.can_finalize_chapter
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
from ui.backend.app.services.truth_files import (
    AuditResult, TruthBundle, TruthFilesIntegration,
)


def _fresh_db_with_chapter() -> str:
    db = os.path.join(tempfile.mkdtemp(), "tfi.db")
    con = sqlite3.connect(db)
    ensure_creation_tables(con)
    con.execute(
        "INSERT INTO projects (project_id, title) VALUES ('p1', 'tfi')"
    )
    con.execute(
        "INSERT INTO chapters (chapter_id, project_id, chapter_num) "
        "VALUES ('ch5', 'p1', 5)"
    )
    con.commit()
    con.close()
    return db


# ─────────── key resolution ───────────


class TestKeyResolution(unittest.TestCase):

    def test_resolves_project_and_num_from_chapter_id(self) -> None:
        db = _fresh_db_with_chapter()
        bundle = TruthFilesIntegration.build_bundle(
            "ch5", db_path=db,
        )
        self.assertEqual(bundle.chapter_id, "ch5")
        self.assertEqual(bundle.chapter_num, 5)
        self.assertEqual(bundle.project_id, "p1")

    def test_explicit_overrides_lookup(self) -> None:
        """Passing project_id + chapter_num skips the chapters lookup —
        useful for callers that already have the values cached."""
        db = _fresh_db_with_chapter()
        bundle = TruthFilesIntegration.build_bundle(
            "ch_does_not_exist", db_path=db,
            project_id="explicit_proj", chapter_num=99,
        )
        self.assertEqual(bundle.project_id, "explicit_proj")
        self.assertEqual(bundle.chapter_num, 99)

    def test_unknown_chapter_returns_empty_bundle(self) -> None:
        db = _fresh_db_with_chapter()
        bundle = TruthFilesIntegration.build_bundle(
            "ch_unknown", db_path=db,
        )
        self.assertTrue(bundle.is_empty)


# ─────────── build_bundle ───────────


class TestBuildBundle(unittest.TestCase):

    def test_empty_truth_tables_yields_wellformed_bundle(self) -> None:
        """No truth_current_state / ledger / hooks data → the bundle
        object is well-formed (no exception). Note: the underlying
        helper emits scaffolding YAML even on empty tables, so
        bundle_text is non-empty but pressured_hooks + ledger_anchors
        are vacuously empty."""
        db = _fresh_db_with_chapter()
        bundle = TruthFilesIntegration.build_bundle("ch5", db_path=db)
        self.assertIsInstance(bundle, TruthBundle)
        # pressured_hooks + ledger_anchors are the data-driven fields;
        # they should be vacant on a fresh project.
        self.assertEqual(bundle.pressured_hooks_text, "")
        self.assertEqual(bundle.ledger_anchors, [])

    def test_returns_bundle_when_underlying_helper_yields_text(self) -> None:
        """Mock the helper layer to verify the wrapper forwards text."""
        db = _fresh_db_with_chapter()
        with mock.patch(
            "agents.production.storyland_state_integration.build_state_context",
            return_value="## 当前世界状态\n主角位于幽冥谷。",
        ), mock.patch(
            "agents.production.storyland_state_integration.list_pressured_hooks_text",
            return_value="• [紧迫] 揭示主角身世（已 3 章未推进）",
        ):
            bundle = TruthFilesIntegration.build_bundle("ch5", db_path=db)
        self.assertIn("幽冥谷", bundle.bundle_text)
        self.assertIn("紧迫", bundle.pressured_hooks_text)
        self.assertFalse(bundle.is_empty)


# ─────────── apply_settlement ───────────


class TestApplySettlement(unittest.IsolatedAsyncioTestCase):

    async def test_missing_chapter_returns_pending(self) -> None:
        db = _fresh_db_with_chapter()
        result = await TruthFilesIntegration.apply_settlement(
            "ch_unknown", "some content", db_path=db,
        )
        self.assertIsInstance(result, AuditResult)
        self.assertEqual(result.audit_status, "audit_pending")
        self.assertFalse(result.success)

    async def test_wraps_helper_dict_into_typed_result(self) -> None:
        db = _fresh_db_with_chapter()
        fake_raw = {
            "success": True,
            "audit_status": "audit_passed",
            "issues": [],
            "cross_ref_issues": [],
            "applied_counts": {"truth_current_state": 2},
            "has_errors": False,
            "deltas_hash": "abc123",
            "idempotent_hit": False,
            "error": None,
        }

        async def fake_apply(*args, **kwargs):
            return fake_raw

        with mock.patch(
            "agents.production.storyland_state_integration."
            "extract_and_apply_state_deltas",
            fake_apply,
        ), mock.patch(
            "llm.router.ModelRouter",
        ) as MR:
            MR.return_value.resolve_role.return_value = ("anthropic", "claude")
            result = await TruthFilesIntegration.apply_settlement(
                "ch5", "chapter text here", db_path=db,
            )

        self.assertEqual(result.audit_status, "audit_passed")
        self.assertTrue(result.passed)
        self.assertFalse(result.failed)
        self.assertEqual(result.applied_counts["truth_current_state"], 2)
        self.assertEqual(result.deltas_hash, "abc123")

    async def test_persists_audit_status_to_chapters_table(self) -> None:
        db = _fresh_db_with_chapter()

        async def fake_apply(*args, **kwargs):
            return {
                "success": False,
                "audit_status": "audit_failed",
                "issues": [{"rule_id": "ledger.balance", "severity": "error",
                             "title": "余额对不上"}],
                "cross_ref_issues": [],
                "applied_counts": {},
                "has_errors": True,
                "deltas_hash": "",
                "idempotent_hit": False,
                "error": None,
            }

        with mock.patch(
            "agents.production.storyland_state_integration."
            "extract_and_apply_state_deltas",
            fake_apply,
        ), mock.patch(
            "llm.router.ModelRouter",
        ) as MR:
            MR.return_value.resolve_role.return_value = ("anthropic", "claude")
            await TruthFilesIntegration.apply_settlement(
                "ch5", "content", db_path=db,
            )

        # Verify audit_status persisted.
        with sqlite3.connect(db) as c:
            row = c.execute(
                "SELECT audit_status, audit_issues_json FROM chapters "
                "WHERE chapter_id = ?", ("ch5",),
            ).fetchone()
        self.assertEqual(row[0], "audit_failed")
        self.assertIn("余额对不上", row[1])

    async def test_helper_exception_yields_pending(self) -> None:
        db = _fresh_db_with_chapter()

        async def boom(*args, **kwargs):
            raise RuntimeError("LLM service down")

        with mock.patch(
            "agents.production.storyland_state_integration."
            "extract_and_apply_state_deltas",
            boom,
        ), mock.patch(
            "llm.router.ModelRouter",
        ) as MR:
            MR.return_value.resolve_role.return_value = ("anthropic", "claude")
            result = await TruthFilesIntegration.apply_settlement(
                "ch5", "content", db_path=db,
            )
        self.assertEqual(result.audit_status, "audit_pending")
        self.assertFalse(result.success)
        self.assertIn("LLM service down", result.error or "")


# ─────────── check_audit_gate ───────────


class TestAuditGate(unittest.TestCase):

    def test_passes_when_no_audit_yet(self) -> None:
        db = _fresh_db_with_chapter()
        allowed, reason = TruthFilesIntegration.check_audit_gate(
            "ch5", db_path=db,
        )
        self.assertTrue(allowed)

    def test_blocks_on_audit_failed(self) -> None:
        db = _fresh_db_with_chapter()
        # Set audit_status='audit_failed' directly.
        from ui.backend.app.services import project_store
        project_store.update_chapter_audit(
            db, "p1", chapter_num=5,
            audit_status="audit_failed",
            audit_issues=[{"rule_id": "test", "severity": "error",
                            "title": "test failure"}],
        )
        allowed, reason = TruthFilesIntegration.check_audit_gate(
            "ch5", db_path=db,
        )
        self.assertFalse(allowed)
        self.assertIn("audit_failed", reason)

    def test_passes_on_unknown_chapter(self) -> None:
        """Fail-open semantics — unknown chapter doesn't block."""
        db = _fresh_db_with_chapter()
        allowed, reason = TruthFilesIntegration.check_audit_gate(
            "ch_unknown", db_path=db,
        )
        self.assertTrue(allowed)


if __name__ == "__main__":
    unittest.main()
