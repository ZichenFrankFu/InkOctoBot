"""Tests for Phase 4 — chapter_snapshots + historical view + snapshot_detector."""
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
from ui.backend.app.services.commit_pipeline import snapshot_writer
from ui.backend.app.services.commit_pipeline.sub_tasks import (
    SubTaskContext, snapshot_detector,
)


def _fresh_db() -> tuple[str, str]:
    db = os.path.join(tempfile.mkdtemp(), "p4.db")
    con = sqlite3.connect(db)
    ensure_creation_tables(con)
    con.execute("INSERT INTO projects (project_id, title) VALUES ('p1','测试')")
    con.commit()
    con.close()
    project_store.save_editor_doc(db, "p1", {
        "volumes": [{"chapters": [{
            "id": "ch11", "chapter_id": "ch11",
            "synopsis": "测试章节",
            "special_requirements": "本章要打斗",
        }]}],
    })
    return db, "ch11"


class TestSnapshotWriter(unittest.TestCase):

    def setUp(self) -> None:
        self.db, self.cid = _fresh_db()
        # capture_snapshot calls load_chapter_fields which reads
        # get_db_path() — point it at our temp DB or
        # special_requirements_snapshot stays empty.
        self._patch = mock.patch(
            "ui.backend.app.services.project_paths.get_db_path",
            return_value=self.db,
        )
        self._patch.start()

    def tearDown(self) -> None:
        self._patch.stop()

    def test_capture_creates_snapshot_row(self) -> None:
        sid = snapshot_writer.capture_snapshot(self.db, "p1", self.cid, 11)
        self.assertTrue(sid.startswith("snap_"))
        snap = snapshot_writer.get_snapshot(self.db, sid)
        self.assertIsNotNone(snap)
        self.assertEqual(snap["chapter_id"], self.cid)
        # special_requirements_snapshot captured from chapter_fields.
        self.assertEqual(snap["special_requirements_snapshot"], "本章要打斗")

    def test_config_dedupes_via_hash(self) -> None:
        snapshot_writer.capture_snapshot(self.db, "p1", self.cid, 11)
        snapshot_writer.capture_snapshot(self.db, "p1", self.cid, 11)
        with sqlite3.connect(self.db) as con:
            snap_count = con.execute(
                "SELECT COUNT(*) FROM chapter_snapshots"
            ).fetchone()[0]
            config_count = con.execute(
                "SELECT COUNT(*) FROM chapter_config_snapshots"
            ).fetchone()[0]
        # Two snapshots, but the same config blobs → no duplicates.
        self.assertEqual(snap_count, 2)
        # 3 config types (project / characters / worldbook); since
        # they're identical across the two captures, we have ≤ 3 rows.
        self.assertLessEqual(config_count, 3)

    def test_capture_does_not_raise_on_missing_data(self) -> None:
        """Even with no chapter_fields / no embedding service available,
        the row should still be written (defensive fallback)."""
        with mock.patch(
            "ui.backend.app.services.embedding.get_embedding_service",
            side_effect=RuntimeError("no service"),
        ):
            sid = snapshot_writer.capture_snapshot(
                self.db, "p1", self.cid, 11,
            )
        snap = snapshot_writer.get_snapshot(self.db, sid)
        self.assertIsNotNone(snap)
        self.assertEqual(snap["embedding_model_used"], "")


class TestSnapshotDetector(unittest.IsolatedAsyncioTestCase):

    def setUp(self) -> None:
        self.db, self.cid = _fresh_db()

    async def test_skipped_when_no_on_stage_characters(self) -> None:
        ctx = SubTaskContext(
            project_id="p1", chapter_id=self.cid, db_path=self.db,
            chapter_text="一些内容", chapter_num=11,
        )
        result = await snapshot_detector.run(ctx)
        self.assertEqual(result["status"], "skipped")

    async def test_emits_notification_for_high_confidence_candidate(self) -> None:
        """Mock the detector to return one high-confidence candidate;
        verify a snapshot_trigger_detected notification gets created."""
        ctx = SubTaskContext(
            project_id="p1", chapter_id=self.cid, db_path=self.db,
            chapter_text="主角觉醒了。", chapter_num=11,
        )
        # Override the on-stage chars so the skip check passes.
        with mock.patch(
            "ui.backend.app.services.commit_pipeline.sub_tasks."
            "snapshot_detector._resolve_on_stage_characters",
            return_value=["主角"],
        ), mock.patch(
            "ui.backend.app.services.snapshot_auto_detector."
            "detect_after_chapter_commit",
            return_value=[
                {"character_name": "主角", "snapshot_id": "sn1",
                 "confidence": 0.85, "reason": "trigger matched"},
            ],
        ):
            result = await snapshot_detector.run(ctx)
        self.assertEqual(result["candidates"], 1)
        self.assertEqual(result["notified"], 1)
        from ui.backend.app.services.notifications import list_notifications
        notifs = list_notifications(self.db, project_id="p1")
        self.assertTrue(any(
            n.notification_type == "snapshot_trigger_detected"
            for n in notifs
        ))

    async def test_does_not_notify_low_confidence(self) -> None:
        ctx = SubTaskContext(
            project_id="p1", chapter_id=self.cid, db_path=self.db,
            chapter_text="一些内容", chapter_num=11,
        )
        with mock.patch(
            "ui.backend.app.services.commit_pipeline.sub_tasks."
            "snapshot_detector._resolve_on_stage_characters",
            return_value=["主角"],
        ), mock.patch(
            "ui.backend.app.services.snapshot_auto_detector."
            "detect_after_chapter_commit",
            return_value=[
                {"character_name": "主角", "confidence": 0.5,
                 "reason": "weak match"},
            ],
        ):
            result = await snapshot_detector.run(ctx)
        self.assertEqual(result["candidates"], 1)
        self.assertEqual(result["notified"], 0)


class TestHistoricalViewAPI(unittest.TestCase):

    def setUp(self) -> None:
        self.db, self.cid = _fresh_db()
        # Resolve the actual chapter_num from the seeded chapter row;
        # Tab 2 reconstructs state at the chapter's actual chapter_num.
        with sqlite3.connect(self.db) as con:
            self.chapter_num = con.execute(
                "SELECT chapter_num FROM chapters WHERE chapter_id = ?",
                (self.cid,),
            ).fetchone()[0]
        # Capture a snapshot so the historical view has something to return.
        self.sid = snapshot_writer.capture_snapshot(
            self.db, "p1", self.cid, self.chapter_num,
        )
        # Seed a state_change_log row at or before chapter_num so Tab 2
        # surfaces it as active state.
        with sqlite3.connect(self.db) as con:
            con.execute(
                "INSERT INTO state_change_log "
                "(log_id, project_id, subject, subject_type, predicate, "
                " new_value, change_chapter, source) "
                "VALUES ('log_h1', 'p1', '主角', 'character', '位于', "
                " '幽冥谷', ?, 'llm_auto')",
                (max(1, self.chapter_num - 1),),
            )
            con.commit()
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from ui.backend.app.routers.historical_view_api import router
        app = FastAPI()
        app.include_router(router)
        self._patch = mock.patch(
            "ui.backend.app.routers.historical_view_api.get_db_path",
            return_value=self.db,
        )
        self._patch.start()
        self.client = TestClient(app)

    def tearDown(self) -> None:
        self._patch.stop()

    def test_list_snapshots(self) -> None:
        res = self.client.get(f"/api/historical-view/snapshots/{self.cid}")
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertEqual(body["chapter_id"], self.cid)
        self.assertEqual(len(body["snapshots"]), 1)
        self.assertEqual(body["snapshots"][0]["snapshot_id"], self.sid)

    def test_get_returns_all_5_tabs(self) -> None:
        res = self.client.get(f"/api/historical-view/{self.cid}")
        self.assertEqual(res.status_code, 200)
        body = res.json()
        for k in ("tab_1_content", "tab_2_storyland_state",
                   "tab_3_reader_memory", "tab_4_full_prompt",
                   "tab_5_diagnostics"):
            self.assertIn(k, body)
        self.assertEqual(body["snapshot_id"], self.sid)

    def test_tab2_reconstructs_state_at_chapter_num(self) -> None:
        res = self.client.get(f"/api/historical-view/{self.cid}")
        body = res.json()
        active = body["tab_2_storyland_state"]["active_state"]
        # Our seeded row for 主角/位于 → 幽冥谷 at chapter 10 should
        # be active at chapter 11.
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0]["subject"], "主角")
        self.assertEqual(active[0]["value"], "幽冥谷")

    def test_unknown_chapter_returns_404(self) -> None:
        res = self.client.get("/api/historical-view/no-such-chapter")
        self.assertEqual(res.status_code, 404)


if __name__ == "__main__":
    unittest.main()
