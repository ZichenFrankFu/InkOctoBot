"""Post-Commit 确认 gate (spec Post-Commit·机制3 + LLM交互·机制2).

With ``post_commit_require_confirmation`` on, the merged extraction is
stored as a pending proposal; nothing reaches the canonical tables
until the user applies it. Discarding leaves the tables untouched.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from storage.project_schema import ensure_creation_tables
from ui.backend.app.services.commit_pipeline.sub_tasks import (
    SubTaskContext, state_extractor,
)


_PARSED = {
    "state_deltas": [{
        "subject": "张远", "subject_type": "character",
        "predicate": "位于", "new_value": "黑风寨", "trigger": "潜入",
    }],
    "ledger_deltas": [], "emotion_deltas": [], "new_entities": [],
    "events": [{
        "event_type": "plot", "description": "张远潜入黑风寨",
        "characters_involved": ["张远"], "importance": "A",
    }],
    "hook_deltas": [{
        "hook_id": None, "description": "黑风寨的内应是谁",
        "action": "new", "scale": "event_clue",
    }],
    "subplot_updates": [],
}


@pytest.fixture
def db(tmp_path: Path) -> str:
    path = tmp_path / "gate.db"
    con = sqlite3.connect(str(path))
    ensure_creation_tables(con)
    con.execute("INSERT INTO projects(project_id, title) VALUES('p1','x')")
    con.execute(
        "INSERT INTO chapters (chapter_id, project_id, chapter_num, title) "
        "VALUES ('ch3', 'p1', 3, '第三章')",
    )
    con.commit()
    con.close()
    return str(path)


@pytest.fixture
def gated(monkeypatch):
    monkeypatch.setattr(state_extractor, "_require_confirmation", lambda: True)


@pytest.fixture
def llm(monkeypatch):
    async def fake_llm(chapter_text, chapter_num, existing, **kw):
        return (_PARSED, "mock/m")
    monkeypatch.setattr(state_extractor, "_call_llm", fake_llm)


def _ctx(db: str) -> SubTaskContext:
    return SubTaskContext(
        project_id="p1", chapter_id="ch3", db_path=db,
        chapter_text="张远夜里潜入黑风寨。", chapter_num=3,
    )


def _counts(db: str) -> dict:
    with sqlite3.connect(db) as con:
        return {
            "spo": con.execute(
                "SELECT COUNT(*) FROM truth_current_state").fetchone()[0],
            "events": con.execute(
                "SELECT COUNT(*) FROM episodic_events").fetchone()[0],
            "hooks": con.execute(
                "SELECT COUNT(*) FROM pending_hooks").fetchone()[0],
        }


@pytest.mark.asyncio
async def test_gate_stores_proposal_writes_nothing(db, gated, llm) -> None:
    out = await state_extractor.run(_ctx(db))
    assert out["status"] == "pending_confirmation"
    assert out["proposal_id"].startswith("psx_")
    assert _counts(db) == {"spo": 0, "events": 0, "hooks": 0}
    with sqlite3.connect(db) as con:
        status, = con.execute(
            "SELECT status FROM pending_state_extractions "
            "WHERE proposal_id = ?", (out["proposal_id"],),
        ).fetchone()
    assert status == "pending"


@pytest.mark.asyncio
async def test_gate_off_writes_directly(db, llm) -> None:
    out = await state_extractor.run(_ctx(db))
    assert out["status"] == "ok"
    c = _counts(db)
    assert c["spo"] == 1 and c["events"] == 1 and c["hooks"] == 1


class TestReviewEndpoints:
    @pytest.fixture
    def client(self, db, monkeypatch) -> TestClient:
        monkeypatch.setattr(
            "ui.backend.app.routers.state_review_api.get_db_path",
            lambda: db,
        )
        from ui.backend.app.main import app
        return TestClient(app)

    @pytest.mark.asyncio
    async def test_apply_persists_all_products(
        self, db, gated, llm, client,
    ) -> None:
        out = await state_extractor.run(_ctx(db))
        pid = out["proposal_id"]
        listed = client.get("/api/state-review/ch3/pending").json()
        assert listed["items"][0]["proposal_id"] == pid
        assert listed["items"][0]["parsed"]["events"][0]["description"] \
            == "张远潜入黑风寨"

        r = client.post(f"/api/state-review/proposals/{pid}/apply")
        assert r.status_code == 200
        body = r.json()
        assert body["state_changes"] == 1
        assert body["events"] == 1
        assert body["hook_nodes"] == 1
        assert _counts(db) == {"spo": 1, "events": 1, "hooks": 1}
        # Proposal resolved; double-apply blocked.
        assert client.post(
            f"/api/state-review/proposals/{pid}/apply").status_code == 409

    @pytest.mark.asyncio
    async def test_discard_leaves_tables_untouched(
        self, db, gated, llm, client,
    ) -> None:
        out = await state_extractor.run(_ctx(db))
        pid = out["proposal_id"]
        r = client.post(f"/api/state-review/proposals/{pid}/discard")
        assert r.status_code == 200
        assert _counts(db) == {"spo": 0, "events": 0, "hooks": 0}
        assert client.get(
            "/api/state-review/ch3/pending").json()["items"] == []

    @pytest.mark.asyncio
    async def test_new_proposal_supersedes_old_pending(
        self, db, gated, llm,
    ) -> None:
        first = await state_extractor.run(_ctx(db))
        second = await state_extractor.run(_ctx(db))
        with sqlite3.connect(db) as con:
            rows = dict(con.execute(
                "SELECT proposal_id, status FROM pending_state_extractions",
            ).fetchall())
        assert rows[first["proposal_id"]] == "discarded"
        assert rows[second["proposal_id"]] == "pending"
