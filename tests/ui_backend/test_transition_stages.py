"""转变态 3 档位 (spec 角色卡·机制2) — 动摇 / 试探 / 倾向."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from storage.project_schema import ensure_creation_tables
from ui.backend.app.services import character_snapshot_resolver as resolver
from ui.backend.app.services import snapshot_store


@pytest.fixture
def db(tmp_path: Path) -> str:
    path = tmp_path / "stages.db"
    con = sqlite3.connect(str(path))
    ensure_creation_tables(con)
    con.execute("INSERT INTO projects(project_id, title) VALUES('p1','x')")
    con.execute(
        "INSERT INTO characters (character_id, project_id, name, role, "
        "personality) VALUES ('c1', 'p1', '张远', '主角', '冷静')",
    )
    con.commit()
    con.close()
    return str(path)


@pytest.fixture
def snap(db: str) -> dict:
    """Transition window: beats at ch 10 & 15, completes at ch 19."""
    return snapshot_store.upsert_snapshot(db, {
        "character_id": "c1", "project_id": "p1",
        "trigger_description": "黑化",
        "personality_override": "冷酷无情",
        "bound_chapters": [10, 15],
        "transition_complete_chapter": 19,
    })


class TestDerivedStage:
    def test_thirds_mapping(self, db, snap) -> None:
        # window [10, 19]: 11 → 动摇; 14 → 试探; 17 → 倾向
        r11 = resolver.resolve(db, "c1", 11)
        assert r11["transition_status"] == "transition_gap"
        assert r11["transition_stage"] == "wavering"
        assert r11["transition_stage_source"] == "derived"
        r14 = resolver.resolve(db, "c1", 14)
        assert r14["transition_stage"] == "probing"
        r17 = resolver.resolve(db, "c1", 17)
        assert r17["transition_stage"] == "leaning"

    def test_event_chapter_also_gets_stage(self, db, snap) -> None:
        r = resolver.resolve(db, "c1", 15)
        assert r["transition_status"] == "transition_event"
        assert r["transition_stage"] in ("wavering", "probing", "leaning")

    def test_stable_and_complete_have_no_stage(self, db, snap) -> None:
        assert "transition_stage" not in resolver.resolve(db, "c1", 5)
        assert "transition_stage" not in resolver.resolve(db, "c1", 19)


class TestUserStageOverride:
    def test_explicit_stage_wins(self, db, snap) -> None:
        snapshot_store.upsert_snapshot(db, {
            **snap, "transition_stages": {"11": "leaning"},
        })
        r = resolver.resolve(db, "c1", 11)
        assert r["transition_stage"] == "leaning"
        assert r["transition_stage_source"] == "user"
        assert r["transition_stage_label"] == "倾向"

    def test_omitted_key_preserves_stages(self, db, snap) -> None:
        snapshot_store.upsert_snapshot(db, {
            **snap, "transition_stages": {"11": "probing"},
        })
        body = {k: v for k, v in snap.items() if k != "transition_stages"}
        snapshot_store.upsert_snapshot(db, body)   # old-client partial PUT
        saved = snapshot_store.get_snapshot(db, snap["snapshot_id"])
        assert saved["transition_stages"] == {"11": "probing"}


class TestStageAPI:
    @pytest.fixture
    def client(self, db, monkeypatch) -> TestClient:
        monkeypatch.setattr(
            "ui.backend.app.routers.snapshot_api._db", lambda: db,
        )
        from ui.backend.app.main import app
        return TestClient(app)

    def test_set_and_clear_stage(self, db, snap, client) -> None:
        sid = snap["snapshot_id"]
        r = client.post(f"/api/snapshots/{sid}/set-stage",
                        json={"chapter_num": 12, "stage": "probing"})
        assert r.status_code == 200
        assert r.json()["transition_stages"] == {"12": "probing"}
        r2 = client.post(f"/api/snapshots/{sid}/set-stage",
                         json={"chapter_num": 12, "stage": None})
        assert r2.json()["transition_stages"] == {}

    def test_invalid_stage_rejected(self, db, snap, client) -> None:
        r = client.post(f"/api/snapshots/{snap['snapshot_id']}/set-stage",
                        json={"chapter_num": 12, "stage": "halfway"})
        assert r.status_code == 400


class TestLoaderRendering:
    def test_stage_guidance_in_card(self, db, snap, monkeypatch) -> None:
        snapshot_store.upsert_snapshot(db, {
            **snap, "transition_stages": {"11": "probing"},
        })
        from ui.backend.app.services.prompt_context.loaders import (
            character_cards,
        )
        resolution = resolver.resolve(db, "c1", 11)
        card = character_cards._render_transition_gap(
            {"id": "c1", "name": "张远", "personality": "冷静"},
            resolution, 11,
        )
        assert "试探" in card
        assert "用户设定" in card
