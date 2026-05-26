"""
Tests for the B2 audit gate (truth_integration.summarize_audit_issues +
project_store audit-status helpers + can_finalize_chapter guard).
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))

from storage.project_schema import ensure_creation_tables
from agents.production import truth_integration as ti
from ui.backend.app.services import project_store


def _fresh_db() -> str:
    db = os.path.join(tempfile.mkdtemp(), "audit.db")
    con = sqlite3.connect(db)
    con.execute("PRAGMA foreign_keys=ON")
    ensure_creation_tables(con)
    con.execute("INSERT INTO projects (project_id, title) VALUES ('p1', 't')")
    con.commit()
    con.close()
    return db


class TestSummarizeAuditIssues(unittest.TestCase):

    def test_empty_returns_empty(self) -> None:
        self.assertEqual(ti.summarize_audit_issues([], None), [])

    def test_meta_inserted_when_counts_present(self) -> None:
        out = ti.summarize_audit_issues([], {"hook_deltas": 2, "state_patches": 1})
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["rule_id"], "_meta")
        self.assertIn("hook_deltas×2", out[0]["observation"])

    def test_errors_sorted_first(self) -> None:
        out = ti.summarize_audit_issues([
            {"rule_id": "ledger.closed_equation", "severity": "warning",
             "message": "x", "location": ""},
            {"rule_id": "hook.transition_valid", "severity": "error",
             "message": "y", "location": "h_1"},
            {"rule_id": "subplot.hook_resolved_subplot_should_advance",
             "severity": "info", "message": "z", "location": ""},
        ], None)
        # Errors first
        self.assertEqual(out[0]["severity"], "error")
        self.assertEqual(out[0]["title"], "终态再变")
        # Then warnings
        self.assertEqual(out[1]["severity"], "warning")
        # Then info
        self.assertEqual(out[2]["severity"], "info")

    def test_cot_fields_present(self) -> None:
        out = ti.summarize_audit_issues([
            {"rule_id": "ledger.matches_current", "severity": "error",
             "message": "anchor 80 vs 75", "location": "李星河/灵石"},
        ], None)
        issue = out[0]
        self.assertEqual(issue["title"], "锚点不符")
        # CoT-style: thought + observation + suggestion all populated
        self.assertTrue(issue["thought"])
        self.assertIn("anchor 80 vs 75", issue["observation"])
        self.assertIn("李星河/灵石", issue["observation"])
        self.assertTrue(issue["suggestion"])

    def test_unknown_rule_gets_generic_title(self) -> None:
        out = ti.summarize_audit_issues([
            {"rule_id": "completely.unknown.rule", "severity": "error",
             "message": "x", "location": ""},
        ], None)
        # Falls back to last-segment of rule_id (max 8 chars)
        self.assertEqual(out[0]["title"], "rule")


class TestChapterAuditPersistence(unittest.TestCase):

    def setUp(self) -> None:
        self.db = _fresh_db()

    def test_update_and_read_audit(self) -> None:
        issues = [
            {"rule_id": "ledger.closed_equation", "severity": "error",
             "title": "账本未闭合", "thought": "x", "observation": "y",
             "suggestion": "z"},
        ]
        project_store.update_chapter_audit(
            self.db, "p1", chapter_num=3,
            audit_status="audit_failed", audit_issues=issues,
        )
        info = project_store.get_chapter_audit(self.db, "p1", 3)
        self.assertEqual(info["audit_status"], "audit_failed")
        self.assertEqual(len(info["audit_issues"]), 1)
        self.assertEqual(info["audit_issues"][0]["rule_id"], "ledger.closed_equation")
        self.assertTrue(info["exists"])

    def test_get_audit_default_when_missing(self) -> None:
        info = project_store.get_chapter_audit(self.db, "p1", 99)
        self.assertEqual(info["audit_status"], "audit_pending")
        self.assertEqual(info["audit_issues"], [])
        self.assertFalse(info["exists"])

    def test_override_changes_status_only_when_failed(self) -> None:
        # audit_passed → override should NOT change anything
        project_store.update_chapter_audit(
            self.db, "p1", 1, audit_status="audit_passed", audit_issues=[],
        )
        project_store.override_chapter_audit(self.db, "p1", 1)
        info = project_store.get_chapter_audit(self.db, "p1", 1)
        self.assertEqual(info["audit_status"], "audit_passed")

        # audit_failed → override moves to audit_overridden
        project_store.update_chapter_audit(
            self.db, "p1", 2, audit_status="audit_failed", audit_issues=[],
        )
        project_store.override_chapter_audit(self.db, "p1", 2)
        info2 = project_store.get_chapter_audit(self.db, "p1", 2)
        self.assertEqual(info2["audit_status"], "audit_overridden")

    def test_can_finalize_blocks_audit_failed(self) -> None:
        project_store.update_chapter_audit(
            self.db, "p1", 1, audit_status="audit_failed", audit_issues=[],
        )
        allowed, reason = project_store.can_finalize_chapter(self.db, "p1", 1)
        self.assertFalse(allowed)
        self.assertIn("audit_failed", reason)

    def test_can_finalize_allows_passed_and_overridden(self) -> None:
        project_store.update_chapter_audit(
            self.db, "p1", 1, audit_status="audit_passed", audit_issues=[],
        )
        allowed, _ = project_store.can_finalize_chapter(self.db, "p1", 1)
        self.assertTrue(allowed)

        project_store.update_chapter_audit(
            self.db, "p1", 2, audit_status="audit_overridden", audit_issues=[],
        )
        allowed2, _ = project_store.can_finalize_chapter(self.db, "p1", 2)
        self.assertTrue(allowed2)

    def test_can_finalize_allows_pending(self) -> None:
        # Pending = settlement hasn't run yet. We don't block finalize on
        # this — settlement is optional. (User could disable truth_settle.)
        allowed, _ = project_store.can_finalize_chapter(self.db, "p1", 99)
        self.assertTrue(allowed)


class TestExtractAndApplyWithAuditGate(unittest.IsolatedAsyncioTestCase):

    async def test_success_path_returns_audit_passed(self) -> None:
        db = _fresh_db()
        router = MagicMock()
        router.generate = AsyncMock()

        class _Resp:
            content = json.dumps({
                "hook_deltas": [
                    {"description": "新埋的伏笔", "action": "new", "importance": "B"},
                ],
            })
        router.generate.return_value = _Resp()

        result = await ti.extract_and_apply_truth_deltas(
            router, "章节正文",
            project_id="p1", db_path=db, chapter_num=1,
        )
        self.assertEqual(result["audit_status"], "audit_passed")
        self.assertFalse(result["has_errors"])
        self.assertIsInstance(result["issues"], list)

    async def test_parse_failure_returns_audit_failed_with_cot(self) -> None:
        db = _fresh_db()
        router = MagicMock()

        class _Resp:
            content = "garbage non-json"
        router.generate = AsyncMock(return_value=_Resp())

        result = await ti.extract_and_apply_truth_deltas(
            router, "x", project_id="p1", db_path=db, chapter_num=1,
        )
        self.assertEqual(result["audit_status"], "audit_failed")
        self.assertTrue(result["has_errors"])
        # CoT structure present
        self.assertEqual(result["issues"][0]["title"], "结算失败")
        self.assertIn("thought", result["issues"][0])
        self.assertIn("suggestion", result["issues"][0])


class TestLedgerAnchors(unittest.TestCase):

    def test_empty_when_no_history(self) -> None:
        db = _fresh_db()
        anchors = ti.build_ledger_anchors(
            "p1", db, chapter_num=5, characters=["李星河"],
        )
        self.assertEqual(anchors, [])

    def test_returns_latest_per_key(self) -> None:
        db = _fresh_db()
        # Seed two ledger entries for the same character/key, different chapters
        with sqlite3.connect(db) as c:
            c.execute(
                "INSERT INTO character_ledger (ledger_id, project_id, "
                "character_name, chapter_num, category, key, operation, "
                "delta, new_value, reason, in_text_evidence) "
                "VALUES ('l1', 'p1', '李星河', 1, 'resource', '灵石', "
                "'add', 50, 50, 'init', 'x')"
            )
            c.execute(
                "INSERT INTO character_ledger (ledger_id, project_id, "
                "character_name, chapter_num, category, key, operation, "
                "delta, new_value, reason, in_text_evidence) "
                "VALUES ('l2', 'p1', '李星河', 3, 'resource', '灵石', "
                "'add', 30, 80, '收获', 'x')"
            )
            c.commit()

        # Anchors as of chapter 5 should show the latest entry (chapter 3)
        anchors = ti.build_ledger_anchors(
            "p1", db, chapter_num=5, characters=["李星河"],
        )
        self.assertEqual(len(anchors), 1)
        self.assertEqual(anchors[0]["current_value"], 80)
        self.assertEqual(anchors[0]["last_chapter"], 3)

    def test_excludes_current_chapter(self) -> None:
        """Anchors should be as-of (N-1), not include chapter N itself."""
        db = _fresh_db()
        with sqlite3.connect(db) as c:
            c.execute(
                "INSERT INTO character_ledger (ledger_id, project_id, "
                "character_name, chapter_num, category, key, operation, "
                "delta, new_value, reason, in_text_evidence) "
                "VALUES ('l1', 'p1', '李星河', 5, 'resource', '灵石', "
                "'add', 50, 50, 'init', 'x')"
            )
            c.commit()
        # Asking for chapter 5 → should NOT include the chapter-5 entry
        anchors = ti.build_ledger_anchors(
            "p1", db, chapter_num=5, characters=["李星河"],
        )
        self.assertEqual(anchors, [])


if __name__ == "__main__":
    unittest.main()
