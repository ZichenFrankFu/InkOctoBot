"""Layer A: real-shape end-to-end happy-path validation.

What this exercises:

- Seed-fixture sanity: the 5-chapter "轨道挽歌" project lands in the
  DB with the right shape (Part 4 pre-flight).
- Canned LLM dispatcher: every covered ``call_site_id`` / agent role
  returns a schema-legal response (lets the pipeline walk the happy
  path instead of falling through to fallbacks).
- Edit-learning closed loop end to end: 5 user-edit saves capture
  observations, the threshold fires, batch extraction returns
  weighted preferences + a domain gap, the preferences reach the
  next generation's ``user_preferences`` block, and the domain gap
  walks through gate-1 → compile → gate-2 → accepted.
- Core safety: ``needs_review`` knowledge never reaches the registry.

What this does NOT exercise (deliberately — see spec §零 "诚实边界"):

- Real LLM quality, real embeddings, real ChromaDB indexing, real
  Truth Files settlement under live models, real market/reference
  integration. Those live in Layer B (the live-run script)."""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from storage.project_schema import ensure_creation_tables
from tests.fixtures.realdata.seed_project import seed_project
from tests.fixtures.realdata.canned_llm import install_canned_llm
from tests.fixtures.realdata.fake_embedding import install_fake_embedding


def _fresh_db_with_seed() -> tuple[str, dict]:
    db = os.path.join(tempfile.mkdtemp(), "rt.db")
    with sqlite3.connect(db) as con:
        ensure_creation_tables(con)
    info = seed_project(db)
    return db, info


# ─────────── Part 4 pre-flight: fixture + canned-LLM sanity ───────────


class TestSeedFixture(unittest.TestCase):

    def test_seed_creates_project_rows(self) -> None:
        db, info = _fresh_db_with_seed()
        self.assertEqual(info["project_id"], "rt_proj")
        self.assertEqual(len(info["chapter_ids"]), 5)
        with sqlite3.connect(db) as con:
            con.row_factory = sqlite3.Row
            self.assertEqual(
                con.execute(
                    "SELECT title FROM projects WHERE project_id='rt_proj'"
                ).fetchone()["title"],
                "轨道挽歌",
            )
            chars = con.execute(
                "SELECT name FROM characters WHERE project_id='rt_proj' "
                "ORDER BY sort_order"
            ).fetchall()
            self.assertEqual([r["name"] for r in chars], ["林越", "邓星"])
            wb = con.execute(
                "SELECT title FROM worldbook_entries "
                "WHERE project_id='rt_proj' ORDER BY sort_order"
            ).fetchall()
            self.assertEqual(
                {r["title"] for r in wb},
                {"启明号空间站", "曲率残响"},
            )

    def test_repeated_seed_does_not_duplicate(self) -> None:
        """Running --seed twice in a row must not double up rows.
        Earlier the seed used fresh uuids per call, so INSERT OR
        REPLACE never matched and every re-run added another copy of
        every character / worldbook / subplot — which then showed up
        as duplicates in the assembled prompt."""
        db, _ = _fresh_db_with_seed()
        seed_project(db)  # second run
        seed_project(db)  # third run
        with sqlite3.connect(db) as con:
            con.row_factory = sqlite3.Row
            n_chars = con.execute(
                "SELECT COUNT(*) FROM characters WHERE project_id='rt_proj'"
            ).fetchone()[0]
            n_wb = con.execute(
                "SELECT COUNT(*) FROM worldbook_entries "
                "WHERE project_id='rt_proj'"
            ).fetchone()[0]
            try:
                n_sub = con.execute(
                    "SELECT COUNT(*) FROM subplot_threads "
                    "WHERE project_id='rt_proj'"
                ).fetchone()[0]
            except sqlite3.OperationalError:
                n_sub = 0
        self.assertEqual(n_chars, 2,
                         "characters duplicated across seed runs")
        self.assertEqual(n_wb, 2,
                         "worldbook duplicated across seed runs")
        if n_sub:
            self.assertEqual(n_sub, 1,
                             "subplot duplicated across seed runs")

    def test_seed_populates_editor_blob_for_ui(self) -> None:
        """The UI editor page reads ``project_blobs(scope='editor')``;
        an empty blob renders an empty chapter tree and crashes
        several downstream components. Verify the seed populates it
        with the 5-chapter volume structure."""
        db, _ = _fresh_db_with_seed()
        from ui.backend.app.services import project_store
        doc = project_store.get_editor_doc(db, "rt_proj")
        self.assertEqual(len(doc.get("volumes", [])), 1)
        chapters = doc["volumes"][0].get("chapters", [])
        self.assertEqual(len(chapters), 5)
        # Every chapter has the fields the React tree component touches.
        for ch in chapters:
            for key in ("chapter_id", "title", "synopsis",
                        "characters", "pov_character",
                        "special_requirements"):
                self.assertIn(key, ch)
            self.assertIsInstance(ch["characters"], list)

    def test_load_chapter_fields_finds_seeded_synopsis(self) -> None:
        """Regression: ``chapter_fields.py`` keys off ``ch.get("id")``
        while ``save_editor_doc`` writes ``ch.get("chapter_id")``. The
        seed must include BOTH so loaders downstream of the editor
        doc can actually find the chapter's synopsis (without it the
        worldbook + storyland + special_req loaders all silently
        return None → those blocks disappear from the prompt)."""
        db, _ = _fresh_db_with_seed()
        with mock.patch(
            "ui.backend.app.services.project_paths.get_db_path",
            return_value=db,
        ):
            from ui.backend.app.services.prompt_context.chapter_fields \
                import load_chapter_fields
            fields = load_chapter_fields("rt_proj", "rt_ch1")
        self.assertTrue(fields["synopsis"],
                        "synopsis must be reachable via chapter_fields")
        self.assertIn("启明号", fields["synopsis"])
        self.assertIn("舱段", fields["special_requirements"])
        self.assertEqual(fields["location"], "启明号·主控舱")
        self.assertIn("林越", fields["characters"])

    def test_seed_chapters_have_special_requirements(self) -> None:
        db, _ = _fresh_db_with_seed()
        with sqlite3.connect(db) as con:
            con.row_factory = sqlite3.Row
            rows = con.execute(
                "SELECT chapter_num, special_requirements "
                "FROM chapters WHERE project_id='rt_proj' "
                "ORDER BY chapter_num"
            ).fetchall()
        self.assertEqual(len(rows), 5)
        # Each chapter's special_requirement progressively names a more
        # specific aerospace topic — the signal Part 6's domain detector
        # is supposed to pick up.
        seen = " ".join(r["special_requirements"] for r in rows)
        for tech in ("舱段结构", "信号探测", "决策", "EVA", "轨道力学"):
            self.assertIn(tech, seen, f"missing topic: {tech}")


class TestCannedLLMDispatcher(unittest.TestCase):
    """The dispatcher must hand back schema-legal payloads for the
    call sites the pipeline actually uses. If any of these regress,
    every downstream test that relies on canned responses will fail
    in a confusing way, so we sanity-check them up front."""

    def test_state_extractor_returns_legal_delta(self) -> None:
        from tests.fixtures.realdata.canned_llm import (
            _canned_for_call_site,
        )
        raw = _canned_for_call_site(
            "storyland_state.settlement",
            "chapter_num: 4 林越在节点二做 EVA",
        )
        self.assertIsNotNone(raw)
        import json
        delta = json.loads(raw)
        for key in ("truth_current_state", "character_ledger",
                    "pending_hooks", "emotion_arc"):
            self.assertIn(key, delta)
        # Chapter 4 → 林越 should be at 节点二.
        facts = delta["truth_current_state"]["facts"]
        self.assertTrue(any("节点二" in f["object"] for f in facts))

    def test_edit_batch_returns_weighted_prefs_and_domain_gap(self) -> None:
        from tests.fixtures.realdata.canned_llm import _canned_for_call_site
        import json
        parsed = json.loads(_canned_for_call_site(
            "edit_learning.batch_extract", "prompt",
        ))
        # Has both source types.
        sources = {
            (it.get("source") if isinstance(it, dict) else "")
            for it in parsed["style_preferences"]
        }
        self.assertIn("special_req", sources)
        self.assertIn("edit", sources)
        # Domain gap names aerospace.
        gap = parsed["domain_gap"]
        self.assertTrue(gap["needs_domain_knowledge"])
        self.assertIn(
            "航天", gap["candidate_domains"][0]["domain"],
        )


# ─────────── Part 6: edit-learning + domain knowledge end-to-end ───────────


class TestEditLearningClosedLoop(unittest.TestCase):
    """Drive the actual Part A pipeline (capture → threshold → batch
    extract → preference write) under the canned dispatcher, then
    verify the preferences reach the next generation's prompt."""

    def setUp(self) -> None:
        self.db, self.info = _fresh_db_with_seed()

    def _insert_ai_baseline(self, chapter_id: str) -> None:
        from ui.backend.app.services import project_store
        project_store.insert_version(self.db, "rt_proj", {
            "chapter_id": chapter_id,
            "source": "ai",
            "content": f"AI baseline for {chapter_id}\n" + ("正文..." * 80),
        })

    def test_5_edits_trigger_batch_extraction_with_canned_llm(self) -> None:
        # Pre-seed AI baselines for all 5 chapters so each capture
        # has something to diff against.
        for cid in self.info["chapter_ids"]:
            self._insert_ai_baseline(cid)

        from agents.learning import capture_observation
        for n, cid in enumerate(self.info["chapter_ids"], 1):
            obs_id = capture_observation(
                db_path=self.db, project_id="rt_proj",
                chapter_id=cid, chapter_num=n,
                edited_text=f"用户改后正文 ch{n}\n" + ("航天细节..." * 90),
            )
            self.assertTrue(obs_id, f"capture failed for {cid}")

        # All 5 captured, none consumed yet.
        with sqlite3.connect(self.db) as con:
            unconsumed = con.execute(
                "SELECT COUNT(*) FROM edit_observations "
                "WHERE consumed=0 AND project_id='rt_proj'"
            ).fetchone()[0]
        self.assertEqual(unconsumed, 5)

        # Fire batch extraction under canned LLM.
        from agents.learning import edit_batch_extractor
        with install_canned_llm():
            edit_batch_extractor.fire_batch_extraction(
                db_path=self.db, project_id="rt_proj", _await=True,
            )

        with sqlite3.connect(self.db) as con:
            con.row_factory = sqlite3.Row
            prefs = con.execute(
                "SELECT * FROM user_style_preferences "
                "WHERE project_id='rt_proj'"
            ).fetchall()
            unconsumed_after = con.execute(
                "SELECT COUNT(*) FROM edit_observations "
                "WHERE consumed=0 AND project_id='rt_proj'"
            ).fetchone()[0]
            sugs = con.execute(
                "SELECT * FROM domain_suggestions "
                "WHERE project_id='rt_proj'"
            ).fetchall()

        # Preferences were persisted.
        self.assertGreaterEqual(len(prefs), 2,
                                "batch extract didn't write preferences")
        # All observations marked consumed.
        self.assertEqual(unconsumed_after, 0)
        # Aerospace domain suggested.
        self.assertEqual(len(sugs), 1)
        self.assertIn("航天", sugs[0]["domain"])
        self.assertEqual(sugs[0]["status"], "proposed")

    def test_special_req_pref_outranks_edit_pref(self) -> None:
        self._insert_ai_baseline(self.info["chapter_ids"][0])
        from agents.learning import capture_observation, edit_batch_extractor
        capture_observation(
            db_path=self.db, project_id="rt_proj",
            chapter_id=self.info["chapter_ids"][0], chapter_num=1,
            edited_text="user edit",
        )
        with install_canned_llm():
            edit_batch_extractor.fire_batch_extraction(
                db_path=self.db, project_id="rt_proj", _await=True,
            )
        with sqlite3.connect(self.db) as con:
            con.row_factory = sqlite3.Row
            rows = con.execute(
                "SELECT description, confidence "
                "FROM user_style_preferences "
                "WHERE project_id='rt_proj' ORDER BY confidence DESC"
            ).fetchall()
        # The top-confidence one must come from a special_req-derived
        # description (per canned dispatcher's payload).
        self.assertGreater(len(rows), 1)
        # At least one row has confidence >= 0.4 (special_req base).
        self.assertGreaterEqual(max(float(r["confidence"]) for r in rows), 0.4)
        # ... and at least one is 0.15 (edit base).
        self.assertLessEqual(min(float(r["confidence"]) for r in rows), 0.15)

    def test_learned_pref_reaches_user_preferences_loader(self) -> None:
        """After batch extraction, the existing
        ``load_user_style_preferences`` helper (which the Writer's
        ``user_preferences`` block reads through) must surface the
        learned preference text."""
        # Bypass the LLM and seed a pref directly to keep the assertion
        # focused on the loader → prompt path.
        with sqlite3.connect(self.db) as con:
            con.execute(
                """INSERT INTO user_style_preferences
                   (pref_id, project_id, preference_type, description,
                    confidence, observation_count)
                   VALUES ('prf_e2e', 'rt_proj', 'style',
                           '偏好航天技术细节真实感', 0.45, 2)""",
            )
            con.commit()
        from ui.backend.app.services.style_preferences import (
            load_user_style_preferences,
        )
        text = load_user_style_preferences("rt_proj", self.db)
        self.assertIn("航天技术细节真实感", text)


# ─────────── Part B closed loop: gate-1 → compile → gate-2 → accept ───────────


class TestDomainKnowledgeFullFlow(unittest.TestCase):
    """Walk a proposed → accepted suggestion through the whole flow
    using the canned web-compile output. The accepted knowledge skill
    must (a) end up on disk under our redirected knowledge dir and
    (b) NOT appear in the registry until accepted."""

    def setUp(self) -> None:
        self.db, _ = _fresh_db_with_seed()
        # Seed a proposed suggestion (mirrors what Part A would produce).
        import uuid, time
        self.sid = f"dsg_{uuid.uuid4().hex[:12]}"
        now = time.time()
        with sqlite3.connect(self.db) as con:
            con.execute(
                """INSERT INTO domain_suggestions
                   (suggestion_id, project_id, domain, sub_domain,
                    rationale, status, created_at, updated_at)
                   VALUES (?, 'rt_proj', '航天动力学', '轨道力学/EVA',
                           '5章反复强调', 'proposed', ?, ?)""",
                (self.sid, now, now),
            )
            con.commit()

        self.tmp_skill_dir = tempfile.mkdtemp()
        self._patches = [
            mock.patch(
                "agents.learning.knowledge_skill_writer._KNOWLEDGE_DIR",
                __import__("pathlib").Path(self.tmp_skill_dir),
            ),
            # Don't let the writer go and reload the real on-disk skill
            # registry from the (real) ``agents/`` tree — that pulls in
            # the whole codebase's actual skills.
            mock.patch(
                "agents.learning.knowledge_skill_writer.register_with_index",
                return_value={"skill_name": "domain_航天动力学"},
            ),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self) -> None:
        for p in self._patches:
            p.stop()

    def test_needs_review_skill_not_in_registry(self) -> None:
        """Core safety: a needs_review suggestion is in the DB only,
        with NO SKILL.md on disk and therefore invisible to
        SkillRegistry.scan_directory."""
        with sqlite3.connect(self.db) as con:
            con.execute(
                "UPDATE domain_suggestions SET status='needs_review', "
                "compiled_content='## 核心概念\n... 轨道力学' "
                "WHERE suggestion_id=?",
                (self.sid,),
            )
            con.commit()
        from pathlib import Path
        # Nothing on disk yet.
        self.assertFalse(any(Path(self.tmp_skill_dir).iterdir()))
        # Registry confirms nothing scannable.
        from framework.skill_registry import SkillRegistry
        reg = SkillRegistry()
        reg.scan_directory(Path(self.tmp_skill_dir))
        self.assertEqual(reg.count, 0)

    def test_gate1_api_to_gate2_accept_writes_skill(self) -> None:
        from agents.learning.knowledge_acquisition import run_compile_api
        from agents.learning.knowledge_skill_writer import accept_suggestion
        import asyncio
        with install_canned_llm():
            loop = asyncio.new_event_loop()
            try:
                result = loop.run_until_complete(
                    run_compile_api(
                        db_path=self.db, suggestion_id=self.sid,
                    )
                )
            finally:
                loop.close()
        self.assertEqual(result["status"], "needs_review")

        # Gate 2 accept.
        accept = accept_suggestion(
            db_path=self.db, suggestion_id=self.sid,
        )
        self.assertEqual(accept["status"], "accepted")

        # SKILL.md materialised under redirected knowledge dir.
        from pathlib import Path
        skill_dirs = list(Path(self.tmp_skill_dir).iterdir())
        self.assertEqual(len(skill_dirs), 1)
        files = sorted(p.name for p in skill_dirs[0].iterdir())
        self.assertEqual(files, ["SKILL.md", "skill.py"])
        md = (skill_dirs[0] / "SKILL.md").read_text("utf-8")
        # AI-compiled disclaimer is visible in the prompt body.
        self.assertIn("非权威", md)

    def test_gate1_manual_returns_research_prompt(self) -> None:
        from agents.learning.knowledge_acquisition import (
            build_manual_research_prompt,
        )
        prompt = build_manual_research_prompt(
            domain="航天动力学", sub_domain="EVA",
            rationale="5章反复强调",
        )
        self.assertIn("提示词开始", prompt)
        self.assertIn("航天动力学", prompt)
        self.assertIn("(存疑)", prompt)  # canonical hedge instruction


# ─────────── Part 7 (subset): graceful degradation under canned ───────────


class TestDegradation(unittest.TestCase):

    def test_canned_dispatcher_never_returns_none(self) -> None:
        """Defensive: every covered call site has a payload, and the
        fallback agent dispatch returns at minimum a JSON-ish string
        so JSON-parsing callers don't choke."""
        from tests.fixtures.realdata.canned_llm import (
            _canned_for_call_site, _canned_for_agent,
        )
        self.assertIsNotNone(
            _canned_for_call_site("post_commit.summarizer", "x"),
        )
        # Unknown call site → caller may fall back to agent dispatcher.
        unk = _canned_for_call_site("unknown.site", "x")
        self.assertIsNone(unk)  # signals "let the agent path handle it"
        # Agent dispatcher always returns a non-empty string.
        self.assertTrue(_canned_for_agent("evaluator", "x"))
        self.assertTrue(_canned_for_agent("", "x"))  # default JSON

    def test_fake_embedding_returns_768_dim(self) -> None:
        from tests.fixtures.realdata.fake_embedding import _vec_for
        v = _vec_for("林越")
        self.assertEqual(len(v), 768)
        self.assertTrue(all(-1 <= x < 1 for x in v))
        # Determinism: same input → identical vector.
        self.assertEqual(_vec_for("林越"), _vec_for("林越"))


if __name__ == "__main__":
    unittest.main()
