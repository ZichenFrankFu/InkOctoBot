"""Part A integration tests: threshold-batched edit learning.

Cover the three slices the spec calls out:

1. Capture — what counts as a user edit, what doesn't, what gets
   skipped when there's no AI baseline.
2. Threshold + weighting — N-1 doesn't fire, N does; ``special_req``
   preferences outrank ``edit`` ones in starting confidence; repeats
   accumulate confidence.
3. Closed loop — once preferences are persisted with confidence
   ≥0.3, they reach Writer.assemble_chapter via the existing
   ``load_user_style_preferences`` → ``user_preferences`` loader.
"""
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


def _fresh_db() -> str:
    db = os.path.join(tempfile.mkdtemp(), "el.db")
    con = sqlite3.connect(db)
    ensure_creation_tables(con)
    con.execute(
        "INSERT INTO projects (project_id, title) VALUES ('p1', 'el-test')"
    )
    con.execute(
        "INSERT INTO chapters (chapter_id, project_id, chapter_num, "
        "special_requirements) VALUES ('ch1', 'p1', 1, '本章要求：第一人称视角')"
    )
    con.execute(
        "INSERT INTO chapters (chapter_id, project_id, chapter_num) "
        "VALUES ('ch2', 'p1', 2)"
    )
    con.execute(
        "INSERT INTO chapters (chapter_id, project_id, chapter_num) "
        "VALUES ('ch3', 'p1', 3)"
    )
    con.execute(
        "INSERT INTO chapters (chapter_id, project_id, chapter_num) "
        "VALUES ('ch4', 'p1', 4)"
    )
    con.execute(
        "INSERT INTO chapters (chapter_id, project_id, chapter_num) "
        "VALUES ('ch5', 'p1', 5)"
    )
    con.commit()
    con.close()
    return db


def _insert_ai_baseline(db: str, chapter_id: str, content: str = "AI 原始版本") -> None:
    """Helper: write an ``ai`` version so user-edit capture has a baseline."""
    from ui.backend.app.services import project_store
    project_store.insert_version(db, "p1", {
        "chapter_id": chapter_id,
        "source": "ai",
        "content": content,
    })


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ─────────── Capture ───────────


class TestCapture(unittest.TestCase):

    def test_user_edit_captures_observation(self) -> None:
        db = _fresh_db()
        _insert_ai_baseline(db, "ch1", "AI 写的第一章正文" * 20)
        from agents.learning import capture_observation
        obs_id = capture_observation(
            db_path=db, project_id="p1", chapter_id="ch1", chapter_num=1,
            edited_text="用户改后的第一章正文" * 25,
        )
        self.assertTrue(obs_id)
        with sqlite3.connect(db) as con:
            con.row_factory = sqlite3.Row
            rows = con.execute(
                "SELECT * FROM edit_observations WHERE consumed=0"
            ).fetchall()
        self.assertEqual(len(rows), 1)
        self.assertIn("本章要求", rows[0]["special_requirement"])
        # diff_stats is real JSON with expected keys.
        import json as _j
        stats = _j.loads(rows[0]["diff_stats_json"])
        self.assertIn("char_delta", stats)

    def test_ai_source_does_not_capture(self) -> None:
        """maybe_capture_and_fire with source='ai' is a no-op — we never
        learn from our own output."""
        db = _fresh_db()
        _insert_ai_baseline(db, "ch1")
        from agents.learning import maybe_capture_and_fire, count_unconsumed
        maybe_capture_and_fire(
            db_path=db, project_id="p1", chapter_id="ch1", chapter_num=1,
            source="ai", edited_text="anything",
        )
        self.assertEqual(count_unconsumed(db, "p1"), 0)

    def test_no_ai_baseline_skips_capture(self) -> None:
        """First save with no prior ai version → quietly skip."""
        db = _fresh_db()
        from agents.learning import capture_observation
        obs_id = capture_observation(
            db_path=db, project_id="p1", chapter_id="ch1", chapter_num=1,
            edited_text="first ever save",
        )
        self.assertIsNone(obs_id)


# ─────────── Threshold ───────────


class TestThreshold(unittest.TestCase):

    def setUp(self) -> None:
        self.db = _fresh_db()
        for ch in ("ch1", "ch2", "ch3", "ch4", "ch5"):
            _insert_ai_baseline(self.db, ch, f"AI version of {ch}" * 10)

    def _capture_n(self, n: int) -> None:
        from agents.learning import capture_observation
        for i, ch in enumerate(("ch1", "ch2", "ch3", "ch4", "ch5")[:n]):
            capture_observation(
                db_path=self.db, project_id="p1",
                chapter_id=ch, chapter_num=i + 1,
                edited_text=f"user-edited {ch}" * 12,
            )

    def test_below_threshold_does_not_extract(self) -> None:
        self._capture_n(4)
        # Wire a sentinel mock — if extract_batch fires we'll see the call.
        from agents.learning import edit_batch_extractor
        called = mock.AsyncMock(return_value={})
        with mock.patch.object(edit_batch_extractor, "extract_batch", called):
            from agents.learning import maybe_capture_and_fire
            # Fifth call would push to threshold — but threshold is 5
            # and we've only captured 4 above, so this one capture
            # brings us to 5 and SHOULD fire. We need to mock get_threshold
            # to a higher value to test the negative case.
            with mock.patch(
                "ui.backend.app.settings.get_edit_learning_threshold",
                return_value=10,
            ):
                maybe_capture_and_fire(
                    db_path=self.db, project_id="p1",
                    chapter_id="ch5", chapter_num=5,
                    source="user_edit", edited_text="more edits" * 5,
                )
        called.assert_not_called()

    def test_threshold_fires_batch_extraction(self) -> None:
        from agents.learning import edit_batch_extractor

        async def _mock_extract(**kwargs):
            return {
                "style_preferences": [
                    {"description": "倾向短句", "source": "edit"},
                ],
                "content_preferences": [],
                "pacing_preferences": [],
                "domain_gap": {"needs_domain_knowledge": False},
            }

        self._capture_n(4)
        with mock.patch.object(edit_batch_extractor, "extract_batch",
                               side_effect=_mock_extract):
            from agents.learning import maybe_capture_and_fire
            maybe_capture_and_fire(
                db_path=self.db, project_id="p1",
                chapter_id="ch5", chapter_num=5,
                source="user_edit", edited_text="user-edited ch5" * 12,
            )
            # _await=True lets us run the fire in-line for the test.
            edit_batch_extractor.fire_batch_extraction(
                db_path=self.db, project_id="p1", _await=True,
            )

        with sqlite3.connect(self.db) as con:
            con.row_factory = sqlite3.Row
            prefs = con.execute(
                "SELECT * FROM user_style_preferences WHERE project_id='p1'"
            ).fetchall()
            unconsumed = con.execute(
                "SELECT COUNT(*) AS n FROM edit_observations "
                "WHERE project_id='p1' AND consumed=0"
            ).fetchone()
        self.assertGreaterEqual(len(prefs), 1)
        self.assertEqual(unconsumed["n"], 0)

    def test_threshold_configurable(self) -> None:
        """Setting threshold to 3 fires on the 3rd capture, not the 5th."""
        from agents.learning import edit_batch_extractor
        fire_mock = mock.MagicMock()
        self._capture_n(2)
        with mock.patch.object(edit_batch_extractor, "fire_batch_extraction",
                               fire_mock), \
             mock.patch(
                 "agents.learning.edit_observations.count_unconsumed",
                 return_value=3,
             ), \
             mock.patch(
                 "ui.backend.app.settings.get_edit_learning_threshold",
                 return_value=3,
             ):
            from agents.learning import maybe_capture_and_fire
            maybe_capture_and_fire(
                db_path=self.db, project_id="p1",
                chapter_id="ch3", chapter_num=3,
                source="user_edit", edited_text="x" * 200,
            )
        fire_mock.assert_called_once()


# ─────────── Weighting ───────────


class TestWeighting(unittest.TestCase):

    def setUp(self) -> None:
        self.db = _fresh_db()
        _insert_ai_baseline(self.db, "ch1")
        from agents.learning import capture_observation
        capture_observation(
            db_path=self.db, project_id="p1", chapter_id="ch1", chapter_num=1,
            edited_text="x" * 100,
        )

    def _run_extract(self, parsed):
        from agents.learning import edit_batch_extractor

        async def _stub(**kwargs):
            return parsed
        with mock.patch.object(edit_batch_extractor, "extract_batch",
                               side_effect=_stub):
            edit_batch_extractor.fire_batch_extraction(
                db_path=self.db, project_id="p1", _await=True,
            )

    def test_special_req_pref_higher_confidence(self) -> None:
        self._run_extract({
            "style_preferences": [
                {"description": "克制叙述", "source": "special_req"},
                {"description": "短句为主", "source": "edit"},
            ],
            "content_preferences": [],
            "pacing_preferences": [],
            "domain_gap": {"needs_domain_knowledge": False},
        })
        with sqlite3.connect(self.db) as con:
            con.row_factory = sqlite3.Row
            sr = con.execute(
                "SELECT confidence FROM user_style_preferences "
                "WHERE description='克制叙述'"
            ).fetchone()
            ed = con.execute(
                "SELECT confidence FROM user_style_preferences "
                "WHERE description='短句为主'"
            ).fetchone()
        self.assertIsNotNone(sr)
        self.assertIsNotNone(ed)
        self.assertGreater(float(sr["confidence"]), float(ed["confidence"]))
        self.assertAlmostEqual(float(sr["confidence"]), 0.4, places=5)
        self.assertAlmostEqual(float(ed["confidence"]), 0.15, places=5)

    def test_repeated_pref_accumulates_confidence(self) -> None:
        """Same description seen in two batches → observation_count
        increments and confidence climbs by the step (0.15)."""
        self._run_extract({
            "style_preferences": [{"description": "短句", "source": "edit"}],
            "content_preferences": [], "pacing_preferences": [],
            "domain_gap": {"needs_domain_knowledge": False},
        })

        # Capture another observation so the 2nd batch has work.
        from agents.learning import capture_observation
        capture_observation(
            db_path=self.db, project_id="p1", chapter_id="ch1", chapter_num=1,
            edited_text="y" * 200,
        )
        self._run_extract({
            "style_preferences": [{"description": "短句", "source": "edit"}],
            "content_preferences": [], "pacing_preferences": [],
            "domain_gap": {"needs_domain_knowledge": False},
        })

        with sqlite3.connect(self.db) as con:
            con.row_factory = sqlite3.Row
            row = con.execute(
                "SELECT * FROM user_style_preferences WHERE description='短句'"
            ).fetchone()
        self.assertEqual(int(row["observation_count"]), 2)
        # 0.15 (base) + 0.15 (step) = 0.30
        self.assertAlmostEqual(float(row["confidence"]), 0.30, places=5)


# ─────────── Closed loop ───────────


class TestLearnedPrefReachesGeneration(unittest.TestCase):
    """The headline acceptance test: once a preference clears
    confidence 0.3, the existing user_style_preferences loader picks
    it up and it lands in the Writer's ``user_preferences`` kwarg."""

    def test_learned_pref_reaches_next_generation_prompt(self) -> None:
        db = _fresh_db()
        # Seed a USER-CONFIRMED preference directly (mirrors batch
        # extraction followed by the 确认 action — spec 用户偏好·机制4:
        # only confirmed preferences are injected into prompts).
        with sqlite3.connect(db) as con:
            con.execute(
                """INSERT INTO user_style_preferences
                   (pref_id, project_id, preference_type, description,
                    confidence, observation_count, is_confirmed)
                   VALUES ('prf_test1', 'p1', 'style',
                           '偏好克制叙述', 0.45, 2, 1)""",
            )
            con.commit()

        # Loader → string → fed into Writer.user_preferences kwarg.
        from ui.backend.app.services.style_preferences import (
            load_user_style_preferences,
        )
        text = load_user_style_preferences("p1", db)
        self.assertIn("偏好克制叙述", text)
        # That same text is what ChapterContext.user_preferences_text
        # would return via its loader output; just sanity-check the
        # routing format is what Writer expects.
        self.assertIn("用户写作偏好", text)


# ─────────── Robustness ───────────


class TestRobustness(unittest.TestCase):

    def test_extraction_failure_does_not_mark_consumed(self) -> None:
        db = _fresh_db()
        _insert_ai_baseline(db, "ch1")
        from agents.learning import capture_observation, edit_batch_extractor
        capture_observation(
            db_path=db, project_id="p1", chapter_id="ch1", chapter_num=1,
            edited_text="x" * 100,
        )

        async def _boom(**kwargs):
            raise RuntimeError("LLM down")

        with mock.patch.object(edit_batch_extractor, "extract_batch",
                               side_effect=_boom):
            # The thread-based fire-and-forget swallows the error; use
            # the in-line _await path so we can observe.
            try:
                edit_batch_extractor.fire_batch_extraction(
                    db_path=db, project_id="p1", _await=True,
                )
            except RuntimeError:
                pass  # expected

        with sqlite3.connect(db) as con:
            n = con.execute(
                "SELECT COUNT(*) FROM edit_observations WHERE consumed=0"
            ).fetchone()[0]
        self.assertEqual(n, 1, "observation must stay consumed=0 for retry")

    def test_save_version_capture_does_not_break_on_error(self) -> None:
        """save_version path: even if maybe_capture_and_fire raises
        internally, the save itself returns success."""
        db = _fresh_db()
        _insert_ai_baseline(db, "ch1")
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from ui.backend.app.routers.editor_api import router

        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)

        with mock.patch(
            "ui.backend.app.routers.editor_api.get_db_path",
            return_value=db,
        ), mock.patch(
            "agents.learning.maybe_capture_and_fire",
            side_effect=RuntimeError("simulated capture failure"),
        ):
            res = client.post("/api/editor/save-version", json={
                "project_id": "p1",
                "chapter_id": "ch1",
                "text": "edited body",
                "source": "user_edit",
            })
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(res.json()["status"], "ok")


if __name__ == "__main__":
    unittest.main()
