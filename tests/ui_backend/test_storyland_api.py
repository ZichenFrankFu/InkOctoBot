"""/api/storyland — Storyland 页面数据面 (spec 6.4.5)."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from storage.project_schema import ensure_creation_tables


@pytest.fixture
def client(tmp_path: Path, monkeypatch) -> TestClient:
    db = tmp_path / "sl.db"
    con = sqlite3.connect(str(db))
    ensure_creation_tables(con)
    con.execute("INSERT INTO projects(project_id, title) VALUES('p1','x')")
    con.execute(
        "INSERT INTO truth_current_state (triple_id, project_id, subject, "
        "subject_type, predicate, object, valid_from_chapter, "
        "valid_to_chapter) "
        "VALUES ('t1', 'p1', '张远', 'character', '位于', '青云山', 0, 4)",
    )
    con.execute(
        "INSERT INTO truth_current_state (triple_id, project_id, subject, "
        "subject_type, predicate, object, valid_from_chapter) "
        "VALUES ('t2', 'p1', '张远', 'character', '位于', '黑风寨', 5)",
    )
    con.commit()
    con.close()
    monkeypatch.setattr(
        "ui.backend.app.routers.storyland_api._db", lambda: str(db),
    )
    from ui.backend.app.main import app
    return TestClient(app)


class TestState:
    def test_timeline_view_active_only(self, client) -> None:
        r = client.get("/api/storyland/state", params={"project_id": "p1"})
        items = r.json()["items"]
        assert [i["triple_id"] for i in items] == ["t2"]

    def test_chapter_view_window_filter(self, client) -> None:
        r = client.get("/api/storyland/state",
                       params={"project_id": "p1", "chapter_num": 3})
        items = r.json()["items"]
        assert [i["object"] for i in items] == ["青云山"]

    def test_include_closed(self, client) -> None:
        r = client.get("/api/storyland/state",
                       params={"project_id": "p1", "include_closed": "true"})
        assert len(r.json()["items"]) == 2


class TestSubplots:
    def test_crud_and_single_main_rule(self, client) -> None:
        r = client.post("/api/storyland/subplots", json={
            "project_id": "p1", "name": "复仇之路", "thread_type": "main",
            "description": "主角复仇主线",
        })
        assert r.status_code == 200
        tid = r.json()["thread_id"]
        # 故事线·机制1: 有且仅有一条主线
        dup = client.post("/api/storyland/subplots", json={
            "project_id": "p1", "name": "第二主线", "thread_type": "main",
        })
        assert dup.status_code == 409
        client.post("/api/storyland/subplots", json={
            "project_id": "p1", "name": "宗门大比", "thread_type": "sub",
        })
        items = client.get("/api/storyland/subplots",
                           params={"project_id": "p1"}).json()["items"]
        assert [i["thread_type"] for i in items] == ["sub", "main"] or \
               {i["thread_type"] for i in items} == {"main", "sub"}
        # update + delete
        assert client.put(f"/api/storyland/subplots/{tid}",
                          json={"status": "building"}).status_code == 200
        assert client.put(f"/api/storyland/subplots/{tid}",
                          json={"status": "nope"}).status_code == 400
        assert client.delete(f"/api/storyland/subplots/{tid}").status_code == 200


class TestHooks:
    def test_create_derives_window_from_scale(self, client) -> None:
        r = client.post("/api/storyland/hooks", json={
            "project_id": "p1", "description": "玉佩之谜",
            "scale": "boomerang", "origin_chapter": 5,
        })
        assert r.status_code == 200
        assert r.json()["expected_payoff_chapter"] == 8

    def test_world_truth_no_window(self, client) -> None:
        r = client.post("/api/storyland/hooks", json={
            "project_id": "p1", "description": "终极真相",
            "scale": "world_truth", "origin_chapter": 1,
        })
        assert r.json()["expected_payoff_chapter"] is None

    def test_update_scale_and_delete(self, client) -> None:
        hook_id = client.post("/api/storyland/hooks", json={
            "project_id": "p1", "description": "线索甲",
        }).json()["hook_id"]
        assert client.put(f"/api/storyland/hooks/{hook_id}",
                          json={"scale": "grand_plan"}).status_code == 200
        assert client.put(f"/api/storyland/hooks/{hook_id}",
                          json={"scale": "huge"}).status_code == 400
        assert client.delete(f"/api/storyland/hooks/{hook_id}").status_code == 200
        assert client.delete(f"/api/storyland/hooks/{hook_id}").status_code == 404
