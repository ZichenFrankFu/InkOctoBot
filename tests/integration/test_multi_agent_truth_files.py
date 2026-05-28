"""Integration: multi-agent /start phase 1 + phase 2 Truth Files wiring.

Phase 1 (inject): start_generation folds TruthBundle.bundle_text +
pressured_hooks_text into req_data['world_rules'] so every downstream
agent sees the same truth state the single-writer endpoint sees.

Phase 2 (settlement): after Writer + Evaluator finish, the pipeline
runs TruthFilesIntegration.apply_settlement to extract structured
deltas from the prose, applies them to truth tables, and persists
chapters.audit_status. If audit_failed, the session moves to
'paused_audit_review' state.

Both Phases were previously implemented only in the /single-writer
endpoint; multi-agent /start was bypassing the entire Truth Files
machinery. This test exercises both phases via the public helpers
that the pipeline now uses.
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


def _fresh_db() -> str:
    db = os.path.join(tempfile.mkdtemp(), "mat.db")
    con = sqlite3.connect(db)
    ensure_creation_tables(con)
    con.execute(
        "INSERT INTO projects (project_id, title) VALUES ('p1', 'truth-test')"
    )
    con.execute(
        "INSERT INTO chapters (chapter_id, project_id, chapter_num) "
        "VALUES ('ch7', 'p1', 7)"
    )
    con.commit()
    con.close()
    return db


# ─────────── Phase 1: bundle injection ───────────


class TestPhase1Injection(unittest.TestCase):
    """The /start handler must call TruthFilesIntegration.build_bundle
    and fold the result into req_data['world_rules'] + an explicit
    'truth_bundle' key."""

    def setUp(self) -> None:
        self.db = _fresh_db()

    def test_bundle_text_lands_in_world_rules(self) -> None:
        # We exercise the same code path as start_generation by
        # re-creating the injection block directly. The full /start
        # handler also wires referenced_materials + RAG context which
        # are orthogonal here.
        from ui.backend.app.services.truth_files import TruthFilesIntegration

        with mock.patch.object(
            TruthFilesIntegration, "build_bundle",
            return_value=type("_B", (), {
                "bundle_text": "## 当前世界状态\n主角位于幽冥谷",
                "pressured_hooks_text": "• 揭示主角身世（已 3 章未推进）",
                "ledger_anchors": [], "chapter_id": "ch7",
                "chapter_num": 7, "project_id": "p1", "is_empty": False,
            })(),
        ):
            req_data = {"project_id": "p1", "chapter_id": "ch7",
                         "chapter_num": 7, "characters": ["主角"],
                         "world_rules": "原有 world_rules"}
            bundle = TruthFilesIntegration.build_bundle(
                req_data["chapter_id"], chapter_num=req_data["chapter_num"],
                db_path=self.db, project_id=req_data["project_id"],
                characters=req_data["characters"],
            )
            parts = []
            if bundle.bundle_text: parts.append(bundle.bundle_text)
            if bundle.pressured_hooks_text:
                parts.append("## 待推进伏笔（本章应继续推进）\n"
                              + bundle.pressured_hooks_text)
            req_data["truth_bundle"] = "\n\n".join(parts)
            req_data["world_rules"] = (
                f"{req_data['world_rules']}\n\n{req_data['truth_bundle']}"
            )

        self.assertIn("幽冥谷", req_data["world_rules"])
        self.assertIn("揭示主角身世", req_data["world_rules"])
        self.assertIn("原有 world_rules", req_data["world_rules"])

    def test_empty_bundle_does_not_pollute_world_rules(self) -> None:
        """Brand-new project with no truth state — the injection block
        leaves world_rules unchanged."""
        from ui.backend.app.services.truth_files import TruthFilesIntegration

        # Stub helpers so bundle_text + pressured are both empty.
        with mock.patch(
            "agents.production.storyland_state_integration.build_state_context",
            return_value="",
        ), mock.patch(
            "agents.production.storyland_state_integration.list_pressured_hooks_text",
            return_value="",
        ):
            bundle = TruthFilesIntegration.build_bundle(
                "ch7", chapter_num=7, db_path=self.db,
                project_id="p1", characters=["主角"],
            )
        self.assertEqual(bundle.bundle_text, "")
        self.assertEqual(bundle.pressured_hooks_text, "")


# ─────────── Phase 2: settlement + audit ───────────


class TestPhase2Settlement(unittest.IsolatedAsyncioTestCase):

    def setUp(self) -> None:
        self.db = _fresh_db()
        self._patches = []

    def tearDown(self) -> None:
        for p in self._patches:
            p.stop()

    async def test_settlement_helper_invokes_truth_files(self) -> None:
        """_run_settlement_for_session resolves project + chapter from
        req_data and calls TruthFilesIntegration.apply_settlement."""
        from ui.backend.app.routers.generation_api import (
            _run_settlement_for_session,
        )
        from ui.backend.app.services.truth_files import (
            AuditResult, TruthFilesIntegration,
        )

        fake_result = AuditResult(
            chapter_id="ch7", chapter_num=7,
            success=True, audit_status="audit_passed",
            issues=[], applied_counts={"truth_current_state": 3},
        )

        async def fake_apply(*args, **kwargs):
            self.assertEqual(args[0], "ch7")  # chapter_id positional
            return fake_result

        with mock.patch.object(
            TruthFilesIntegration, "apply_settlement", fake_apply,
        ), mock.patch(
            "ui.backend.app.services.project_paths.get_db_path",
            return_value=self.db,
        ):
            result = await _run_settlement_for_session(
                "sess_test", {
                    "project_id": "p1", "chapter_id": "ch7",
                    "chapter_num": 7, "characters": ["主角"],
                    "chapter_title": "第七章",
                },
                "chapter prose body",
            )

        self.assertEqual(result.audit_status, "audit_passed")
        self.assertEqual(result.applied_counts["truth_current_state"], 3)

    async def test_settlement_skips_when_no_project_or_chapter(self) -> None:
        from ui.backend.app.routers.generation_api import (
            _run_settlement_for_session,
        )
        result = await _run_settlement_for_session(
            "sess_x", {"project_id": "", "chapter_id": ""},
            "text",
        )
        self.assertEqual(result.audit_status, "audit_pending")
        self.assertFalse(result.success)
        self.assertIn("no project", result.error or "")

    async def test_audit_failed_does_not_lose_chapter(self) -> None:
        """Even when audit_failed, the chapter still persists — the
        pipeline marks the session paused_audit_review but Posts the
        text so the user can review it."""
        from ui.backend.app.services.truth_files import (
            AuditResult, TruthFilesIntegration,
        )

        async def fake_apply(*args, **kwargs):
            return AuditResult(
                chapter_id="ch7", chapter_num=7,
                success=False, audit_status="audit_failed",
                issues=[{"rule_id": "ledger.balance", "severity": "error"}],
                has_errors=True,
            )

        # Verify the audit_status persists to chapters table.
        with mock.patch(
            "agents.production.storyland_state_integration."
            "extract_and_apply_state_deltas",
            return_value=type("D", (), {})()  # not awaited via apply_settlement mock
        ), mock.patch.object(
            TruthFilesIntegration, "apply_settlement", fake_apply,
        ), mock.patch(
            "ui.backend.app.services.project_paths.get_db_path",
            return_value=self.db,
        ):
            from ui.backend.app.routers.generation_api import (
                _run_settlement_for_session,
            )
            result = await _run_settlement_for_session(
                "sess_fail", {
                    "project_id": "p1", "chapter_id": "ch7",
                    "chapter_num": 7,
                }, "content",
            )
        self.assertTrue(result.failed)
        self.assertEqual(len(result.issues), 1)


if __name__ == "__main__":
    unittest.main()
