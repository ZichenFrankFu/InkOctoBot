"""/api/preferences — 偏好确认 gate 的 REST 层 (spec 用户偏好·机制4)."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from storage.project_schema import ensure_creation_tables


@pytest.fixture
def client(tmp_path: Path, monkeypatch) -> TestClient:
    db = tmp_path / "api.db"
    conn = sqlite3.connect(str(db))
    ensure_creation_tables(conn)
    conn.execute("INSERT INTO projects(project_id, title) VALUES('p1','x')")
    conn.execute(
        "INSERT INTO user_style_preferences (pref_id, project_id, "
        "preference_type, description, confidence, observation_count, "
        "is_confirmed, source_chapters_json, source_type) VALUES "
        "('prf_a', 'p1', 'style', '短句优先', 0.4, 2, 0, '[1,3]', 'edit_inference'), "
        "('prf_b', 'p1', 'pacing', '快节奏开篇', 0.6, 3, 1, '[5]', 'special_requirements')",
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(
        "ui.backend.app.routers.preferences_api._db_path",
        lambda: str(db),
    )
    from ui.backend.app.main import app
    return TestClient(app)


class TestList:
    def test_list_all_with_pending_count(self, client: TestClient) -> None:
        r = client.get("/api/preferences", params={"project_id": "p1"})
        assert r.status_code == 200
        body = r.json()
        assert len(body["items"]) == 2
        assert body["pending_count"] == 1
        by_id = {i["pref_id"]: i for i in body["items"]}
        assert by_id["prf_a"]["source_chapters"] == [1, 3]
        assert by_id["prf_a"]["is_confirmed"] is False
        assert by_id["prf_b"]["source_type"] == "special_requirements"

    def test_filter_pending(self, client: TestClient) -> None:
        r = client.get("/api/preferences",
                       params={"project_id": "p1", "status": "pending"})
        assert [i["pref_id"] for i in r.json()["items"]] == ["prf_a"]


class TestMutations:
    def test_confirm_then_unconfirm(self, client: TestClient) -> None:
        assert client.post("/api/preferences/prf_a/confirm").status_code == 200
        r = client.get("/api/preferences",
                       params={"project_id": "p1", "status": "confirmed"})
        assert {i["pref_id"] for i in r.json()["items"]} == {"prf_a", "prf_b"}
        assert client.post("/api/preferences/prf_a/unconfirm").status_code == 200
        r = client.get("/api/preferences",
                       params={"project_id": "p1", "status": "pending"})
        assert [i["pref_id"] for i in r.json()["items"]] == ["prf_a"]

    def test_edit_description_and_type(self, client: TestClient) -> None:
        r = client.put("/api/preferences/prf_a", json={
            "description": "短句优先，长句不超过30字",
            "preference_type": "content",
        })
        assert r.status_code == 200
        items = client.get(
            "/api/preferences", params={"project_id": "p1"},
        ).json()["items"]
        a = next(i for i in items if i["pref_id"] == "prf_a")
        assert a["description"] == "短句优先，长句不超过30字"
        assert a["preference_type"] == "content"

    def test_invalid_type_rejected(self, client: TestClient) -> None:
        r = client.put("/api/preferences/prf_a",
                       json={"preference_type": "tempo"})
        assert r.status_code == 400

    def test_delete(self, client: TestClient) -> None:
        assert client.delete("/api/preferences/prf_a").status_code == 200
        items = client.get(
            "/api/preferences", params={"project_id": "p1"},
        ).json()["items"]
        assert [i["pref_id"] for i in items] == ["prf_b"]

    def test_missing_pref_404(self, client: TestClient) -> None:
        assert client.post("/api/preferences/nope/confirm").status_code == 404
