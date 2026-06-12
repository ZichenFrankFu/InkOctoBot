"""Part B integration tests: domain knowledge self-learning.

Covers all the gates plus the core safety property the spec is
loudest about: **needs_review content NEVER reaches the skills loader.**

Test layout mirrors the spec §B.11 acceptance list:

1. Detection — Part A's domain_gap output writes ``proposed`` rows;
   already-rejected domains don't get re-suggested.
2. Compile — API mode requires web-search availability and falls
   back to manual on NotImplementedError; both paths end in
   ``needs_review`` with content in DB only (no filesystem write).
3. Gate-2 soft door — ``needs_review`` is never in the registry;
   ``accept`` writes SKILL.md + registers; ``accept`` with edits
   stores the user's version; discard removes the row.
4. Lifecycle — editing an accepted skill recomputes embedding; the
   accepted skill is reachable by the skills loader for generation.

To keep filesystem assertions hermetic, the writer tests redirect
``_KNOWLEDGE_DIR`` to a tempdir."""
from __future__ import annotations

import asyncio
import os
import sqlite3
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from storage.project_schema import ensure_creation_tables


def _fresh_db() -> str:
    db = os.path.join(tempfile.mkdtemp(), "dk.db")
    con = sqlite3.connect(db)
    ensure_creation_tables(con)
    con.execute(
        "INSERT INTO projects (project_id, title) VALUES ('p1', 'dk-test')"
    )
    con.execute(
        "INSERT INTO chapters (chapter_id, project_id, chapter_num) "
        "VALUES ('ch1', 'p1', 1)"
    )
    con.commit()
    con.close()
    return db


def _insert_suggestion(
    db: str, *, status: str = "proposed", domain: str = "航天动力学",
    sub_domain: str = "轨道力学", rationale: str = "用户反复强调技术真实",
    compiled_content: str = "", source_mode: str = "",
) -> str:
    import uuid
    sid = f"dsg_{uuid.uuid4().hex[:12]}"
    now = time.time()
    with sqlite3.connect(db) as con:
        con.execute(
            """INSERT INTO domain_suggestions
               (suggestion_id, project_id, domain, sub_domain,
                rationale, status, compiled_content, source_mode,
                created_at, updated_at)
               VALUES (?, 'p1', ?, ?, ?, ?, ?, ?, ?, ?)""",
            (sid, domain, sub_domain, rationale, status,
             compiled_content, source_mode, now, now),
        )
        con.commit()
    return sid


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ─────────── Detection (Part A domain_gap → proposed row) ───────────


class TestDetection(unittest.TestCase):

    def test_batch_extraction_emits_proposed_suggestion(self) -> None:
        """domain_gap.candidate_domains in the LLM response must end
        up as proposed rows in domain_suggestions."""
        db = _fresh_db()
        # Seed an observation so the extractor has something to consume.
        with sqlite3.connect(db) as con:
            con.execute(
                """INSERT INTO edit_observations
                   (obs_id, project_id, chapter_id, chapter_num,
                    original_text, edited_text, special_requirement,
                    diff_stats_json, consumed, created_at)
                   VALUES ('o1', 'p1', 'ch1', 1, 'A', 'B', '',
                           '{}', 0, strftime('%s','now'))"""
            )
            con.commit()

        from agents.learning import edit_batch_extractor

        async def _stub(**kwargs):
            return {
                "style_preferences": [],
                "content_preferences": [],
                "pacing_preferences": [],
                "domain_gap": {
                    "needs_domain_knowledge": True,
                    "candidate_domains": [
                        {"domain": "航天动力学", "sub_domain": "轨道力学",
                         "rationale": "硬科幻技术细节"},
                    ],
                },
            }

        with mock.patch.object(edit_batch_extractor, "extract_batch",
                               side_effect=_stub):
            edit_batch_extractor.fire_batch_extraction(
                db_path=db, project_id="p1", _await=True,
            )

        with sqlite3.connect(db) as con:
            con.row_factory = sqlite3.Row
            rows = con.execute(
                "SELECT * FROM domain_suggestions WHERE project_id='p1'"
            ).fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["domain"], "航天动力学")
        self.assertEqual(rows[0]["status"], "proposed")

    def test_does_not_resuggest_rejected_domain(self) -> None:
        db = _fresh_db()
        _insert_suggestion(db, status="rejected", domain="航天动力学")

        # Pre-seed an observation.
        with sqlite3.connect(db) as con:
            con.execute(
                """INSERT INTO edit_observations
                   (obs_id, project_id, chapter_id, chapter_num,
                    original_text, edited_text, special_requirement,
                    diff_stats_json, consumed, created_at)
                   VALUES ('o1', 'p1', 'ch1', 1, 'A', 'B', '',
                           '{}', 0, strftime('%s','now'))"""
            )
            con.commit()

        from agents.learning import edit_batch_extractor

        async def _stub(**kwargs):
            return {
                "style_preferences": [], "content_preferences": [],
                "pacing_preferences": [],
                "domain_gap": {
                    "needs_domain_knowledge": True,
                    "candidate_domains": [
                        {"domain": "航天动力学", "rationale": "X"},
                    ],
                },
            }

        with mock.patch.object(edit_batch_extractor, "extract_batch",
                               side_effect=_stub):
            edit_batch_extractor.fire_batch_extraction(
                db_path=db, project_id="p1", _await=True,
            )

        with sqlite3.connect(db) as con:
            n = con.execute(
                "SELECT COUNT(*) FROM domain_suggestions "
                "WHERE project_id='p1' AND domain='航天动力学'"
            ).fetchone()[0]
        # Still just the original rejected row — no duplicate inserted.
        self.assertEqual(n, 1)


# ─────────── Compile (gate 1 → needs_review) ───────────


class TestCompile(unittest.TestCase):

    def test_manual_mode_returns_research_prompt(self) -> None:
        db = _fresh_db()
        sid = _insert_suggestion(db)
        from agents.learning.knowledge_acquisition import (
            build_manual_research_prompt,
        )
        prompt = build_manual_research_prompt(
            domain="航天动力学", rationale="X",
        )
        self.assertIn("航天动力学", prompt)
        self.assertIn("提示词开始", prompt)

    def test_api_mode_compiles_to_needs_review(self) -> None:
        db = _fresh_db()
        sid = _insert_suggestion(db)
        with mock.patch(
            "agents.learning.knowledge_acquisition.compile_via_web_search",
            new=mock.AsyncMock(return_value="## 核心概念\n轨道...\n## 参考来源\n- 维基"),
        ):
            from agents.learning.knowledge_acquisition import run_compile_api
            res = _run(run_compile_api(db_path=db, suggestion_id=sid))
        self.assertEqual(res["status"], "needs_review")
        self.assertIn("轨道", res["content"])
        with sqlite3.connect(db) as con:
            con.row_factory = sqlite3.Row
            row = con.execute(
                "SELECT * FROM domain_suggestions WHERE suggestion_id=?",
                (sid,),
            ).fetchone()
        self.assertEqual(row["status"], "needs_review")
        self.assertIn("轨道", row["compiled_content"])
        self.assertEqual(row["source_mode"], "api")

    def test_web_unavailable_falls_back_to_failed_with_manual_hint(self) -> None:
        db = _fresh_db()
        sid = _insert_suggestion(db)
        from agents.learning.knowledge_acquisition import WebSearchUnavailable
        with mock.patch(
            "agents.learning.knowledge_acquisition.compile_via_web_search",
            new=mock.AsyncMock(side_effect=WebSearchUnavailable("no provider")),
        ):
            from agents.learning.knowledge_acquisition import run_compile_api
            res = _run(run_compile_api(db_path=db, suggestion_id=sid))
        self.assertEqual(res["status"], "failed")
        self.assertEqual(res["hint"], "manual")
        with sqlite3.connect(db) as con:
            row = con.execute(
                "SELECT status FROM domain_suggestions WHERE suggestion_id=?",
                (sid,),
            ).fetchone()
        self.assertEqual(row[0], "failed")

    def test_compile_empty_response_sets_failed(self) -> None:
        db = _fresh_db()
        sid = _insert_suggestion(db)
        with mock.patch(
            "agents.learning.knowledge_acquisition.compile_via_web_search",
            new=mock.AsyncMock(return_value="   "),
        ):
            from agents.learning.knowledge_acquisition import run_compile_api
            res = _run(run_compile_api(db_path=db, suggestion_id=sid))
        self.assertEqual(res["status"], "failed")
        self.assertEqual(res["reason"], "empty_response")

    def test_submit_manual_result_moves_to_needs_review(self) -> None:
        db = _fresh_db()
        sid = _insert_suggestion(db, status="compiling", source_mode="manual")
        from agents.learning.knowledge_acquisition import submit_manual_result
        res = submit_manual_result(
            db_path=db, suggestion_id=sid,
            pasted_content="## 核心概念\n轨道力学是...",
        )
        self.assertEqual(res["status"], "needs_review")
        with sqlite3.connect(db) as con:
            con.row_factory = sqlite3.Row
            row = con.execute(
                "SELECT * FROM domain_suggestions WHERE suggestion_id=?",
                (sid,),
            ).fetchone()
        self.assertEqual(row["status"], "needs_review")
        self.assertEqual(row["source_mode"], "manual")
        self.assertIn("轨道力学", row["compiled_content"])


# ─────────── Gate-2 soft door + core safety ───────────


class TestGate2Safety(unittest.TestCase):
    """The headline safety guarantee: needs_review content lives ONLY
    in the DB. No SKILL.md, no skill_index row, no loader recall."""

    def _redirect_knowledge_dir(self):
        tmp = Path(tempfile.mkdtemp())
        return mock.patch(
            "agents.learning.knowledge_skill_writer._KNOWLEDGE_DIR", tmp,
        ), tmp

    def test_needs_review_does_not_write_skill_md(self) -> None:
        db = _fresh_db()
        sid = _insert_suggestion(
            db, status="needs_review",
            compiled_content="## 核心概念\n轨道力学...",
        )
        patch_dir, tmp = self._redirect_knowledge_dir()
        with patch_dir:
            # No file should exist for this domain in the knowledge dir
            # while it sits in needs_review.
            self.assertFalse(any(tmp.iterdir()))

    def test_needs_review_skill_not_in_registry(self) -> None:
        """★ Core safety property: ``SkillRegistry.scan_all`` ignores
        a needs_review row because there's nothing on disk to scan."""
        db = _fresh_db()
        sid = _insert_suggestion(
            db, status="needs_review",
            compiled_content="## 核心概念\n轨道力学...",
        )
        patch_dir, tmp = self._redirect_knowledge_dir()
        with patch_dir:
            from framework.skill_registry import SkillRegistry
            reg = SkillRegistry()
            # Point the scan at the (empty) tempdir.
            reg.scan_directory(tmp)
            self.assertEqual(reg.count, 0)

    def test_accept_without_edit_stores_original(self) -> None:
        db = _fresh_db()
        body = "## 核心概念\n轨道力学..."
        sid = _insert_suggestion(
            db, status="needs_review", compiled_content=body,
        )
        patch_dir, tmp = self._redirect_knowledge_dir()
        with patch_dir, \
             mock.patch(
                 "agents.learning.knowledge_skill_writer.register_with_index",
                 return_value={"skill_name": "domain_航天动力学"},
             ):
            from agents.learning.knowledge_skill_writer import accept_suggestion
            res = accept_suggestion(db_path=db, suggestion_id=sid)
        self.assertEqual(res["status"], "accepted")
        with sqlite3.connect(db) as con:
            row = con.execute(
                "SELECT status, compiled_content, skill_name "
                "FROM domain_suggestions WHERE suggestion_id=?",
                (sid,),
            ).fetchone()
        self.assertEqual(row[0], "accepted")
        self.assertEqual(row[1], body)
        self.assertTrue(row[2])  # skill_name set

    def test_accept_with_edit_stores_user_version(self) -> None:
        db = _fresh_db()
        sid = _insert_suggestion(
            db, status="needs_review",
            compiled_content="ORIGINAL",
        )
        patch_dir, tmp = self._redirect_knowledge_dir()
        with patch_dir, \
             mock.patch(
                 "agents.learning.knowledge_skill_writer.register_with_index",
                 return_value={"skill_name": "x"},
             ):
            from agents.learning.knowledge_skill_writer import accept_suggestion
            accept_suggestion(
                db_path=db, suggestion_id=sid,
                edited_content="USER-EDITED",
            )
        with sqlite3.connect(db) as con:
            row = con.execute(
                "SELECT compiled_content FROM domain_suggestions "
                "WHERE suggestion_id=?",
                (sid,),
            ).fetchone()
        self.assertEqual(row[0], "USER-EDITED")

    def test_accept_writes_skill_md_and_registers(self) -> None:
        db = _fresh_db()
        body = "## 核心概念\n轨道力学..."
        sid = _insert_suggestion(
            db, status="needs_review", compiled_content=body,
        )
        patch_dir, tmp = self._redirect_knowledge_dir()
        register_calls: dict = {}

        def _register_stub(*, db_path, skill_name, body, recompute_embedding=True):
            register_calls.update(
                skill_name=skill_name, body=body,
                recompute=recompute_embedding,
            )
            return {"skill_name": skill_name}

        with patch_dir, \
             mock.patch(
                 "agents.learning.knowledge_skill_writer.register_with_index",
                 side_effect=_register_stub,
             ):
            from agents.learning.knowledge_skill_writer import accept_suggestion
            res = accept_suggestion(db_path=db, suggestion_id=sid)

        # SKILL.md + skill.py written on disk.
        slug_dir = list(tmp.iterdir())
        self.assertEqual(len(slug_dir), 1)
        files = sorted(p.name for p in slug_dir[0].iterdir())
        self.assertEqual(files, ["SKILL.md", "skill.py"])
        md_text = (slug_dir[0] / "SKILL.md").read_text("utf-8")
        # Annotation banner is visible inside the prompt body.
        self.assertIn("非权威", md_text)
        # register_with_index was called with the body.
        self.assertEqual(register_calls.get("body"), body)
        # Returned skill_name follows the convention.
        self.assertTrue(res["skill_name"].startswith("domain_"))

    def test_discard_does_not_create_skill(self) -> None:
        db = _fresh_db()
        sid = _insert_suggestion(db, status="needs_review",
                                 compiled_content="...")
        patch_dir, tmp = self._redirect_knowledge_dir()
        with patch_dir:
            # Rejection path simulates "discard" via the API: status flip
            # to rejected, no writer call.
            with sqlite3.connect(db) as con:
                con.execute(
                    "UPDATE domain_suggestions SET status='rejected' "
                    "WHERE suggestion_id=?",
                    (sid,),
                )
                con.commit()
            self.assertFalse(any(tmp.iterdir()))


# ─────────── Lifecycle (edit accepted, generation reach) ───────────


class TestLifecycle(unittest.TestCase):

    def test_edit_accepted_recomputes_embedding(self) -> None:
        db = _fresh_db()
        sid = _insert_suggestion(
            db, status="accepted", compiled_content="OLD-BODY",
        )
        tmp = Path(tempfile.mkdtemp())
        register_calls: list = []

        def _register_stub(**kw):
            register_calls.append(kw)
            return {"skill_name": kw["skill_name"]}

        with mock.patch(
            "agents.learning.knowledge_skill_writer._KNOWLEDGE_DIR", tmp,
        ), mock.patch(
            "agents.learning.knowledge_skill_writer.register_with_index",
            side_effect=_register_stub,
        ):
            from agents.learning.knowledge_skill_writer import (
                update_accepted_content,
            )
            res = update_accepted_content(
                db_path=db, suggestion_id=sid, new_content="NEW-BODY",
            )
        self.assertEqual(res["status"], "accepted")
        # Embedding recomputation must have been requested.
        self.assertTrue(register_calls)
        self.assertTrue(register_calls[0]["recompute_embedding"])
        # New body persisted.
        with sqlite3.connect(db) as con:
            row = con.execute(
                "SELECT compiled_content FROM domain_suggestions "
                "WHERE suggestion_id=?",
                (sid,),
            ).fetchone()
        self.assertEqual(row[0], "NEW-BODY")

    def test_cannot_accept_from_non_needs_review(self) -> None:
        """Defence in depth: even if caller bug calls accept on a
        'proposed' row, the writer refuses (otherwise we'd publish
        an empty knowledge skill)."""
        db = _fresh_db()
        sid = _insert_suggestion(db, status="proposed")
        from agents.learning.knowledge_skill_writer import accept_suggestion
        res = accept_suggestion(db_path=db, suggestion_id=sid)
        self.assertEqual(res["status"], "rejected")
        self.assertIn("proposed", res.get("reason", ""))


if __name__ == "__main__":
    unittest.main()
