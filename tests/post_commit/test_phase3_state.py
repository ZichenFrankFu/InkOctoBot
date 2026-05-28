"""Tests for Phase 3 — state_extractor + review/CRUD API."""
from __future__ import annotations

import json
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
    SubTaskContext, state_extractor,
)


def _fresh_db() -> tuple[str, str]:
    db = os.path.join(tempfile.mkdtemp(), "p3.db")
    con = sqlite3.connect(db)
    ensure_creation_tables(con)
    con.execute("INSERT INTO projects (project_id, title) VALUES ('p1','t')")
    con.commit()
    con.close()
    # Seed a chapter so review endpoints can resolve chapter_num.
    project_store.save_editor_doc(db, "p1", {
        "volumes": [{"chapters": [{
            "id": "ch7", "chapter_id": "ch7", "chapter_num": 7,
            "synopsis": "测试",
        }]}],
    })
    return db, "ch7"


class TestStateDeltas(unittest.IsolatedAsyncioTestCase):

    def setUp(self) -> None:
        self.db, self.cid = _fresh_db()

    async def test_state_writes_truth_and_log(self) -> None:
        ctx = SubTaskContext(
            project_id="p1", chapter_id=self.cid, db_path=self.db,
            chapter_text="主角抵达幽冥谷。", chapter_num=7,
        )
        parsed = {
            "state_deltas": [
                {"subject": "主角", "subject_type": "character",
                 "predicate": "位于", "new_value": "幽冥谷",
                 "trigger": "主角抵达"},
            ],
            "ledger_deltas": [], "emotion_deltas": [], "new_entities": [],
        }
        with mock.patch.object(
            state_extractor, "_call_llm",
            return_value=(parsed, "mock/m"),
        ):
            result = await state_extractor.run(ctx)
        self.assertEqual(result["state_changes"], 1)
        with sqlite3.connect(self.db) as con:
            tcs = con.execute(
                "SELECT subject, predicate, object, valid_from_chapter, "
                "       valid_to_chapter "
                "FROM truth_current_state"
            ).fetchall()
            self.assertEqual(len(tcs), 1)
            self.assertEqual(tcs[0], ("主角", "位于", "幽冥谷", 7, None))
            log = con.execute(
                "SELECT subject, predicate, old_value, new_value, source, "
                "       change_type "
                "FROM state_change_log"
            ).fetchall()
            self.assertEqual(len(log), 1)
            self.assertEqual(log[0],
                             ("主角", "位于", None, "幽冥谷", "llm_auto", "state_change"))

    async def test_state_supersedes_prior_active_row(self) -> None:
        """A second extraction with a different new_value for the same
        (subject, predicate) sets the prior row's valid_to and inserts
        a fresh row at the new chapter."""
        ctx = SubTaskContext(
            project_id="p1", chapter_id=self.cid, db_path=self.db,
            chapter_text="主角抵达幽冥谷。", chapter_num=7,
        )
        first = {"state_deltas": [
            {"subject": "主角", "predicate": "位于", "new_value": "幽冥谷"}],
            "ledger_deltas": [], "emotion_deltas": [], "new_entities": []}
        second = {"state_deltas": [
            {"subject": "主角", "predicate": "位于", "new_value": "青云山"}],
            "ledger_deltas": [], "emotion_deltas": [], "new_entities": []}
        with mock.patch.object(state_extractor, "_call_llm",
                                 return_value=(first, "m1")):
            await state_extractor.run(ctx)
        ctx2 = SubTaskContext(
            project_id="p1", chapter_id=self.cid, db_path=self.db,
            chapter_text="主角动身离去。", chapter_num=10,
        )
        with mock.patch.object(state_extractor, "_call_llm",
                                 return_value=(second, "m2")):
            await state_extractor.run(ctx2)
        with sqlite3.connect(self.db) as con:
            rows = con.execute(
                "SELECT object, valid_from_chapter, valid_to_chapter "
                "FROM truth_current_state ORDER BY valid_from_chapter"
            ).fetchall()
        # First row: object=幽冥谷, from=7, to=9 (superseded)
        # Second row: object=青云山, from=10, to=NULL (active)
        self.assertEqual(rows[0], ("幽冥谷", 7, 9))
        self.assertEqual(rows[1], ("青云山", 10, None))

    async def test_unknown_predicate_gets_warning(self) -> None:
        ctx = SubTaskContext(
            project_id="p1", chapter_id=self.cid, db_path=self.db,
            chapter_text="x", chapter_num=7,
        )
        parsed = {"state_deltas": [
            {"subject": "X", "predicate": "嗅探",   # not in vocab
             "new_value": "Y"}],
            "ledger_deltas": [], "emotion_deltas": [], "new_entities": []}
        with mock.patch.object(state_extractor, "_call_llm",
                                 return_value=(parsed, "m1")):
            await state_extractor.run(ctx)
        with sqlite3.connect(self.db) as con:
            warn = con.execute(
                "SELECT validation_warning FROM state_change_log"
            ).fetchone()
        self.assertIn("unknown_predicate", warn[0])


class TestLedgerDeltas(unittest.IsolatedAsyncioTestCase):

    def setUp(self) -> None:
        self.db, self.cid = _fresh_db()

    async def test_ledger_accumulates_balance(self) -> None:
        ctx = SubTaskContext(
            project_id="p1", chapter_id=self.cid, db_path=self.db,
            chapter_text="x", chapter_num=7,
        )
        parsed = {
            "state_deltas": [], "emotion_deltas": [], "new_entities": [],
            "ledger_deltas": [
                {"character": "主角", "category": "currency", "key": "金币",
                 "delta": 100, "trigger": "卖货"},
                {"character": "主角", "category": "currency", "key": "金币",
                 "delta": -30, "trigger": "买药"},
            ],
        }
        with mock.patch.object(state_extractor, "_call_llm",
                                 return_value=(parsed, "m1")):
            result = await state_extractor.run(ctx)
        self.assertEqual(result["ledger_changes"], 2)
        with sqlite3.connect(self.db) as con:
            rows = con.execute(
                "SELECT operation, delta, new_value FROM character_ledger "
                "ORDER BY created_at"
            ).fetchall()
        self.assertEqual(rows[0], ("add", 100, 100))
        self.assertEqual(rows[1], ("subtract", 30, 70))

    async def test_negative_balance_creates_warning_and_notification(self) -> None:
        ctx = SubTaskContext(
            project_id="p1", chapter_id=self.cid, db_path=self.db,
            chapter_text="x", chapter_num=7,
        )
        parsed = {
            "state_deltas": [], "emotion_deltas": [], "new_entities": [],
            "ledger_deltas": [
                {"character": "穷人", "key": "金币", "delta": -50,
                 "trigger": "败家"},
            ],
        }
        with mock.patch.object(state_extractor, "_call_llm",
                                 return_value=(parsed, "m1")):
            result = await state_extractor.run(ctx)
        self.assertTrue(result["ledger_warnings"])
        from ui.backend.app.services.notifications import list_notifications
        notifs = list_notifications(self.db, project_id="p1")
        types = {n.notification_type for n in notifs}
        self.assertIn("ledger_negative_balance", types)


class TestEmotionDeltas(unittest.IsolatedAsyncioTestCase):

    def setUp(self) -> None:
        self.db, self.cid = _fresh_db()

    async def test_emotion_arc_appended(self) -> None:
        ctx = SubTaskContext(
            project_id="p1", chapter_id=self.cid, db_path=self.db,
            chapter_text="x", chapter_num=7,
        )
        parsed = {
            "state_deltas": [], "ledger_deltas": [], "new_entities": [],
            "emotion_deltas": [
                {"character": "主角", "from_state": "平静",
                 "to_state": "愤怒", "trigger": "被冒犯"},
            ],
        }
        with mock.patch.object(state_extractor, "_call_llm",
                                 return_value=(parsed, "m1")):
            await state_extractor.run(ctx)
        with sqlite3.connect(self.db) as con:
            rows = con.execute(
                "SELECT character_name, from_state, to_state FROM emotion_arcs"
            ).fetchall()
        self.assertEqual(rows, [("主角", "平静", "愤怒")])


class TestEntityFiltering(unittest.IsolatedAsyncioTestCase):
    """5-layer filter:
    Layer 1: exact match → merge as alias
    Layer 2: high embedding similarity → auto-merge or review queue
    Layer 3: mention count threshold
    Layer 4: type filter (concept etc)
    Layer 5: create + flag auto_created
    """

    def setUp(self) -> None:
        self.db, self.cid = _fresh_db()

    async def test_layer4_type_filter(self) -> None:
        """Disallowed type → discarded."""
        ctx = SubTaskContext(
            project_id="p1", chapter_id=self.cid, db_path=self.db,
            chapter_text="x", chapter_num=7,
        )
        parsed = {"state_deltas": [], "ledger_deltas": [], "emotion_deltas": [],
                   "new_entities": [
                       {"name": "宿命", "type": "abstract_idea", "aliases": []},
                   ]}
        with mock.patch.object(state_extractor, "_call_llm",
                                 return_value=(parsed, "m1")):
            result = await state_extractor.run(ctx)
        self.assertEqual(result["entities_discarded"], 1)
        self.assertEqual(result["entities_created"], 0)

    async def test_layer3_mention_threshold(self) -> None:
        """Single mention + no aliases → discarded."""
        ctx = SubTaskContext(
            project_id="p1", chapter_id=self.cid, db_path=self.db,
            chapter_text="幽灵出现了一次。", chapter_num=7,
        )
        parsed = {"state_deltas": [], "ledger_deltas": [], "emotion_deltas": [],
                   "new_entities": [
                       {"name": "幽灵", "type": "character", "aliases": []},
                   ]}
        with mock.patch.object(state_extractor, "_call_llm",
                                 return_value=(parsed, "m1")):
            result = await state_extractor.run(ctx)
        self.assertEqual(result["entities_discarded"], 1)

    async def test_layer5_create_with_multiple_mentions(self) -> None:
        ctx = SubTaskContext(
            project_id="p1", chapter_id=self.cid, db_path=self.db,
            chapter_text="妖兽出现。妖兽很凶。妖兽逃走了。", chapter_num=7,
        )
        parsed = {"state_deltas": [], "ledger_deltas": [], "emotion_deltas": [],
                   "new_entities": [
                       {"name": "妖兽", "type": "character", "aliases": []},
                   ]}
        with mock.patch.object(state_extractor, "_call_llm",
                                 return_value=(parsed, "m1")):
            result = await state_extractor.run(ctx)
        self.assertEqual(result["entities_created"], 1)
        with sqlite3.connect(self.db) as con:
            row = con.execute(
                "SELECT name, auto_created, mention_count "
                "FROM storyland_entities WHERE name='妖兽'"
            ).fetchone()
        self.assertEqual(row, ("妖兽", 1, 3))


# ─────────── HTTP API ───────────


class TestReviewAPI(unittest.TestCase):

    def setUp(self) -> None:
        self.db, self.cid = _fresh_db()
        # Resolve the actual chapter_num the editor saved so seeded
        # state_change_log rows align with the chapter for the
        # review-by-chapter_id lookup.
        with sqlite3.connect(self.db) as con:
            self.chapter_num = con.execute(
                "SELECT chapter_num FROM chapters WHERE chapter_id = ?",
                (self.cid,),
            ).fetchone()[0]
            con.execute(
                "INSERT INTO truth_current_state "
                "(triple_id, project_id, subject, subject_type, predicate, "
                " object, valid_from_chapter) "
                "VALUES ('tcs1', 'p1', '主角', 'character', '位于', '幽冥谷', ?)",
                (self.chapter_num,),
            )
            con.execute(
                "INSERT INTO state_change_log "
                "(log_id, project_id, subject, subject_type, predicate, "
                " new_value, change_chapter, source) "
                "VALUES ('log1', 'p1', '主角', 'character', '位于', '幽冥谷', "
                " ?, 'llm_auto')",
                (self.chapter_num,),
            )
            con.commit()

        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from ui.backend.app.routers.state_review_api import router

        app = FastAPI()
        app.include_router(router)
        # Patch the local binding in the router module — the handler
        # did `from ..project_paths import get_db_path` so patching
        # the source module wouldn't reach it.
        self._patch = mock.patch(
            "ui.backend.app.routers.state_review_api.get_db_path",
            return_value=self.db,
        )
        self._patch.start()
        self.client = TestClient(app)

    def tearDown(self) -> None:
        self._patch.stop()

    def test_tab1_lists_state_changes(self) -> None:
        res = self.client.get(f"/api/state-review/{self.cid}/state")
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertEqual(body["chapter_num"], self.chapter_num)
        self.assertEqual(len(body["changes"]), 1)
        self.assertEqual(body["changes"][0]["new_value"], "幽冥谷")

    def test_correction_appends_log_and_supersedes(self) -> None:
        res = self.client.put(
            "/api/state-changes/log1",
            json={"new_value": "青云山", "trigger": "user fix"},
        )
        self.assertEqual(res.status_code, 200)
        with sqlite3.connect(self.db) as con:
            logs = con.execute(
                "SELECT change_type, new_value, source, parent_log_id "
                "FROM state_change_log ORDER BY recorded_at"
            ).fetchall()
            tcs = con.execute(
                "SELECT object, valid_from_chapter, valid_to_chapter "
                "FROM truth_current_state ORDER BY valid_from_chapter"
            ).fetchall()
        # Original + correction.
        self.assertEqual(len(logs), 2)
        self.assertEqual(logs[1][0], "correction")
        self.assertEqual(logs[1][1], "青云山")
        self.assertEqual(logs[1][3], "log1")
        # Truth: 2 rows, original superseded at chapter_num-1.
        self.assertEqual(len(tcs), 2)
        expected_valid_to = max(1, self.chapter_num - 1)
        self.assertEqual(tcs[0], ("幽冥谷", self.chapter_num, expected_valid_to))
        self.assertEqual(tcs[1], ("青云山", self.chapter_num, None))

    def test_cancellation_appends_log_and_supersedes(self) -> None:
        res = self.client.post("/api/state-changes/log1/cancel")
        self.assertEqual(res.status_code, 200)
        with sqlite3.connect(self.db) as con:
            cancel = con.execute(
                "SELECT change_type, new_value FROM state_change_log "
                "WHERE log_id != 'log1'"
            ).fetchone()
            tcs = con.execute(
                "SELECT valid_to_chapter FROM truth_current_state "
                "WHERE triple_id='tcs1'"
            ).fetchone()
        self.assertEqual(cancel[0], "cancellation")
        self.assertIsNone(cancel[1])
        self.assertEqual(tcs[0], max(1, self.chapter_num - 1))


class TestManualLedger(unittest.TestCase):

    def setUp(self) -> None:
        self.db, self.cid = _fresh_db()
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from ui.backend.app.routers.state_review_api import router
        app = FastAPI()
        app.include_router(router)
        # Patch the local binding in the router module — the handler
        # did `from ..project_paths import get_db_path` so patching
        # the source module wouldn't reach it.
        self._patch = mock.patch(
            "ui.backend.app.routers.state_review_api.get_db_path",
            return_value=self.db,
        )
        self._patch.start()
        self.client = TestClient(app)

    def tearDown(self) -> None:
        self._patch.stop()

    def test_manual_create_then_delete_reverses_balance(self) -> None:
        # Create.
        res = self.client.post("/api/ledger-changes", json={
            "project_id": "p1", "character_name": "甲", "chapter_num": 7,
            "key": "金币", "delta": 100, "trigger": "manual",
        })
        self.assertEqual(res.status_code, 200)
        lid = res.json()["ledger_id"]
        self.assertEqual(res.json()["new_value"], 100)
        # Reverse.
        res2 = self.client.delete(f"/api/ledger-changes/{lid}")
        self.assertEqual(res2.status_code, 200)
        self.assertEqual(res2.json()["new_value"], 0)


class TestEntityReviewQueueDecision(unittest.TestCase):

    def setUp(self) -> None:
        self.db, self.cid = _fresh_db()
        # Seed: one existing entity + one pending review queue item.
        with sqlite3.connect(self.db) as con:
            con.execute(
                "INSERT INTO storyland_entities "
                "(entity_id, project_id, name, entity_type, "
                " introduced_chapter, aliases_json, source) "
                "VALUES ('e1', 'p1', '幽冥谷', 'location', 1, '[]', 'manual')"
            )
            con.execute(
                "INSERT INTO entity_review_queue "
                "(queue_id, project_id, chapter_id, candidate_name, "
                " candidate_type, candidate_aliases_json, "
                " similar_existing_entity_id, similarity_score) "
                "VALUES ('q1', 'p1', ?, '幽冥之谷', 'location', '[]', "
                " 'e1', 0.91)",
                (self.cid,),
            )
            con.commit()
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from ui.backend.app.routers.state_review_api import router
        app = FastAPI()
        app.include_router(router)
        # Patch the local binding in the router module — the handler
        # did `from ..project_paths import get_db_path` so patching
        # the source module wouldn't reach it.
        self._patch = mock.patch(
            "ui.backend.app.routers.state_review_api.get_db_path",
            return_value=self.db,
        )
        self._patch.start()
        self.client = TestClient(app)

    def tearDown(self) -> None:
        self._patch.stop()

    def test_merge_decision_adds_alias_to_target(self) -> None:
        res = self.client.post(
            "/api/entity-review-queue/q1/decision",
            json={"action": "merge"},
        )
        self.assertEqual(res.status_code, 200)
        with sqlite3.connect(self.db) as con:
            aliases = con.execute(
                "SELECT aliases_json FROM storyland_entities "
                "WHERE entity_id='e1'"
            ).fetchone()[0]
            status = con.execute(
                "SELECT status FROM entity_review_queue WHERE queue_id='q1'"
            ).fetchone()[0]
        self.assertIn("幽冥之谷", json.loads(aliases))
        self.assertEqual(status, "merged")

    def test_create_decision_promotes_to_full_entity(self) -> None:
        res = self.client.post(
            "/api/entity-review-queue/q1/decision",
            json={"action": "create"},
        )
        self.assertEqual(res.status_code, 200)
        with sqlite3.connect(self.db) as con:
            count = con.execute(
                "SELECT COUNT(*) FROM storyland_entities WHERE name='幽冥之谷'"
            ).fetchone()[0]
        self.assertEqual(count, 1)

    def test_discard_decision_marks_queue_only(self) -> None:
        res = self.client.post(
            "/api/entity-review-queue/q1/decision",
            json={"action": "discard"},
        )
        self.assertEqual(res.status_code, 200)
        with sqlite3.connect(self.db) as con:
            # No new entity created.
            count = con.execute(
                "SELECT COUNT(*) FROM storyland_entities"
            ).fetchone()[0]
            status = con.execute(
                "SELECT status FROM entity_review_queue WHERE queue_id='q1'"
            ).fetchone()[0]
        self.assertEqual(count, 1)  # only the seeded one
        self.assertEqual(status, "discarded")


if __name__ == "__main__":
    unittest.main()
