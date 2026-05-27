"""Tests for Phase 5 — validator + skill_emitter."""
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
from ui.backend.app.services.commit_pipeline import validator
from ui.backend.app.services.commit_pipeline.sub_tasks import (
    SubTaskContext, skill_emitter,
)


def _fresh_db() -> tuple[str, str]:
    db = os.path.join(tempfile.mkdtemp(), "p5.db")
    con = sqlite3.connect(db)
    ensure_creation_tables(con)
    con.execute("INSERT INTO projects (project_id, title) VALUES ('p1','t')")
    con.commit()
    con.close()
    project_store.save_editor_doc(db, "p1", {
        "volumes": [{"chapters": [{"id": "ch1", "chapter_id": "ch1",
                                      "synopsis": "测试"}]}],
    })
    return db, "ch1"


# ─────────── Validator ───────────


class TestValidatorLayer1(unittest.TestCase):

    def setUp(self) -> None:
        self.db, _ = _fresh_db()

    def test_unknown_predicate_flagged(self) -> None:
        with sqlite3.connect(self.db) as con:
            con.execute(
                "INSERT INTO state_change_log "
                "(log_id, project_id, subject, subject_type, predicate, "
                " new_value, change_chapter, source, change_type) "
                "VALUES ('l1', 'p1', 'X', 'character', '嗅探', 'Y', "
                " 1, 'llm_auto', 'state_change')"
            )
            con.commit()
        issues = validator.run_layer1(self.db, "p1")
        cats = [i.category for i in issues]
        self.assertIn("predicate_invalid", cats)

    def test_negative_balance_flagged(self) -> None:
        with sqlite3.connect(self.db) as con:
            con.execute(
                "INSERT INTO character_ledger "
                "(ledger_id, project_id, character_name, chapter_num, "
                " category, key, operation, delta, new_value) "
                "VALUES ('lg1', 'p1', '甲', 1, 'currency', '金币', "
                " 'subtract', 10, -5)"
            )
            con.commit()
        issues = validator.run_layer1(self.db, "p1")
        cats = [i.category for i in issues]
        self.assertIn("negative_balance", cats)
        # negative_balance is high-severity.
        neg = [i for i in issues if i.category == "negative_balance"][0]
        self.assertEqual(neg.severity, "high")

    def test_no_issues_on_clean_project(self) -> None:
        issues = validator.run_layer1(self.db, "p1")
        self.assertEqual(issues, [])


class TestValidatorLayer2(unittest.TestCase):

    def setUp(self) -> None:
        self.db, _ = _fresh_db()

    def test_duplicate_active_triple_flagged(self) -> None:
        with sqlite3.connect(self.db) as con:
            for tid in ("t1", "t2"):
                con.execute(
                    "INSERT INTO truth_current_state "
                    "(triple_id, project_id, subject, subject_type, "
                    " predicate, object, valid_from_chapter) "
                    "VALUES (?, 'p1', '甲', 'character', '位于', '某地', 1)",
                    (tid,),
                )
            con.commit()
        issues = validator.run_layer2(self.db, "p1")
        cats = [i.category for i in issues]
        self.assertIn("duplicate_triple", cats)


class TestValidatorLayer3(unittest.IsolatedAsyncioTestCase):

    def setUp(self) -> None:
        self.db, _ = _fresh_db()

    async def test_returns_empty_on_no_candidates(self) -> None:
        out = await validator.run_layer3(self.db, "p1", [])
        self.assertEqual(out, [])

    async def test_caps_at_5_candidates(self) -> None:
        """Layer 3 batches max 5 candidates per LLM call."""
        from ui.backend.app.services.commit_pipeline.validator import (
            ValidationIssue,
        )
        ten = [
            ValidationIssue(f"vi_{i}", "medium", "test", f"d{i}", "t", "")
            for i in range(10)
        ]
        seen_prompt: dict = {}

        class _FR:
            async def invoke(self, *, primary_role, prompt, **kw):
                seen_prompt["len"] = prompt.count("issue_id")
                return '{"results": []}'

        with mock.patch(
            "llm.fallback_router.get_fallback_router", return_value=_FR(),
        ):
            await validator.run_layer3(self.db, "p1", ten)
        # 5 issues in the batch each contribute one issue_id mention,
        # plus 1 mention in the JSON schema example at the top of the
        # prompt template. Total = 6 → confirms the 5-item cap held.
        self.assertEqual(seen_prompt["len"], 6)

    async def test_llm_failure_returns_empty(self) -> None:
        """Layer 3 must never break the validator chain — LLM errors
        degrade to []."""
        from ui.backend.app.services.commit_pipeline.validator import (
            ValidationIssue,
        )
        one = [ValidationIssue("vi_x", "medium", "t", "d", "t", "")]

        class _FR:
            async def invoke(self, *args, **kw):
                raise RuntimeError("LLM down")

        with mock.patch(
            "llm.fallback_router.get_fallback_router", return_value=_FR(),
        ):
            out = await validator.run_layer3(self.db, "p1", one)
        self.assertEqual(out, [])


# ─────────── Validator API ───────────


class TestValidatorAPI(unittest.TestCase):

    def setUp(self) -> None:
        self.db, _ = _fresh_db()
        # Seed one negative-balance row so Layer 1 finds an issue.
        with sqlite3.connect(self.db) as con:
            con.execute(
                "INSERT INTO character_ledger "
                "(ledger_id, project_id, character_name, chapter_num, "
                " category, key, operation, delta, new_value) "
                "VALUES ('lg1', 'p1', '甲', 1, 'currency', '金币', "
                " 'subtract', 10, -5)"
            )
            con.commit()
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from ui.backend.app.routers.validator_api import router
        app = FastAPI()
        app.include_router(router)
        self._patch = mock.patch(
            "ui.backend.app.routers.validator_api.get_db_path",
            return_value=self.db,
        )
        self._patch.start()
        self.client = TestClient(app)

    def tearDown(self) -> None:
        self._patch.stop()

    def test_quick_check_returns_issues(self) -> None:
        res = self.client.get("/api/validator/quick?project_id=p1")
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertGreater(body["count"], 0)
        cats = [i["category"] for i in body["issues"]]
        self.assertIn("negative_balance", cats)


# ─────────── skill_emitter ───────────


class TestSkillEmitter(unittest.IsolatedAsyncioTestCase):

    def setUp(self) -> None:
        self.db, self.cid = _fresh_db()

    async def test_skipped_with_only_one_version(self) -> None:
        with sqlite3.connect(self.db) as con:
            con.execute(
                "INSERT INTO text_versions "
                "(version_id, chapter_id, version_num, source, content) "
                "VALUES ('v1', ?, 1, 'user_edit', '只有一版')",
                (self.cid,),
            )
            con.commit()
        ctx = SubTaskContext(
            project_id="p1", chapter_id=self.cid, db_path=self.db,
            chapter_text="只有一版", chapter_num=1,
        )
        result = await skill_emitter.run(ctx)
        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["versions_found"], 1)

    async def test_skipped_when_diff_too_small(self) -> None:
        with sqlite3.connect(self.db) as con:
            for vid, txt in [("v1", "甲" * 100), ("v2", "甲" * 110)]:
                con.execute(
                    "INSERT INTO text_versions "
                    "(version_id, chapter_id, version_num, "
                    " source, content) "
                    "VALUES (?, ?, ?, 'user_edit', ?)",
                    (vid, self.cid, int(vid[1:]), txt),
                )
            con.commit()
        ctx = SubTaskContext(
            project_id="p1", chapter_id=self.cid, db_path=self.db,
            chapter_text="", chapter_num=1,
        )
        result = await skill_emitter.run(ctx)
        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["reason"], "diff_too_small")

    async def test_substantive_diff_writes_skill_stub(self) -> None:
        with sqlite3.connect(self.db) as con:
            for vid, txt in [("v1", "甲" * 100), ("v2", "乙" * 500)]:
                con.execute(
                    "INSERT INTO text_versions "
                    "(version_id, chapter_id, version_num, "
                    " source, content) "
                    "VALUES (?, ?, ?, 'user_edit', ?)",
                    (vid, self.cid, int(vid[1:]), txt),
                )
            con.commit()
        ctx = SubTaskContext(
            project_id="p1", chapter_id=self.cid, db_path=self.db,
            chapter_text="", chapter_num=1,
        )
        result = await skill_emitter.run(ctx)
        self.assertEqual(result["status"], "ok")
        self.assertGreaterEqual(result["diff_size"], 50)


if __name__ == "__main__":
    unittest.main()
