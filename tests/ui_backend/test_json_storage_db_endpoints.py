"""
Integration tests for the DB-backed endpoints in ``routers/json_storage_api.py``.

Exercises the FastAPI app with a temp data dir and verifies that
characters / worldbook / project_memory CRUD now uses the SQLite
project_store (project_memories / characters / worldbook_entries tables).
"""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))


def _isolated_settings(tmp: Path):
    """Force settings to point at our tmp data dir before any router import.

    pydantic-settings caches field defaults at class definition time, so
    setting WN_DATA_DIR alone doesn't help — we have to construct a
    fresh ``Settings`` instance with explicit overrides AND patch the
    module singleton in place.
    """
    os.environ["WN_TEST_MODE"] = "1"
    os.environ["WN_DATA_DIR"] = str(tmp)
    from ui.backend.app import settings as settings_module
    settings_module.settings.data_dir = tmp
    settings_module.settings.test_mode = True
    # Also reset the project_store init cache so the next call to
    # ``ensure_project_row`` re-creates schema in the new DB.
    from ui.backend.app.services import project_store
    project_store._initialized.clear()


def _client(tmp: Path):
    _isolated_settings(tmp)
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from ui.backend.app.routers.json_storage_api import router

    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


class TestCharactersEndpoints(unittest.TestCase):

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.client = _client(self.tmp)

    def _make_proj(self, pid="p1") -> None:
        # Make sure a project row exists in the DB so FK checks pass
        from ui.backend.app.services import project_store
        from ui.backend.app.services.project_paths import get_db_path
        project_store.ensure_project_row(get_db_path(), pid, "test")

    def test_create_list_update_delete(self) -> None:
        self._make_proj()
        # POST create
        resp = self.client.post(
            "/data/characters",
            json={"project_id": "p1", "name": "李星河", "role": "主角"},
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(body["name"], "李星河")
        cid = body["id"]

        # GET list
        resp = self.client.get("/data/characters?project_id=p1")
        items = resp.json()["items"]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["id"], cid)

        # PUT update
        resp = self.client.put(
            f"/data/characters/{cid}",
            json={"project_id": "p1", "name": "李星河",
                  "role": "反派", "tags": ["新标签"]},
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["role"], "反派")

        # GET single
        self.assertEqual(
            self.client.get(f"/data/characters/{cid}").json()["role"], "反派",
        )

        # DELETE
        self.assertEqual(
            self.client.delete(f"/data/characters/{cid}").status_code, 200,
        )
        self.assertEqual(
            self.client.get(f"/data/characters/{cid}").status_code, 404,
        )

    def test_create_duplicate_returns_409(self) -> None:
        self._make_proj()
        self.client.post(
            "/data/characters",
            json={"project_id": "p1", "name": "唯一"},
        )
        resp = self.client.post(
            "/data/characters",
            json={"project_id": "p1", "name": "唯一"},
        )
        self.assertEqual(resp.status_code, 409)

    def test_data_lands_in_sqlite_not_json(self) -> None:
        self._make_proj()
        self.client.post(
            "/data/characters",
            json={"project_id": "p1", "name": "TestChar"},
        )
        # SQLite row exists
        from ui.backend.app.services.project_paths import get_db_path
        with sqlite3.connect(get_db_path()) as c:
            n = c.execute(
                "SELECT COUNT(*) FROM characters WHERE name=?",
                ("TestChar",),
            ).fetchone()[0]
        self.assertEqual(n, 1)
        # No JSON file written for this collection
        self.assertEqual(list((self.tmp / "characters").glob("*.json")) if (self.tmp / "characters").exists() else [], [])


class TestWorldbookEndpoints(unittest.TestCase):

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.client = _client(self.tmp)
        from ui.backend.app.services import project_store
        from ui.backend.app.services.project_paths import get_db_path
        project_store.ensure_project_row(get_db_path(), "p1", "t")

    def test_crud(self) -> None:
        resp = self.client.post(
            "/data/worldbook",
            json={"project_id": "p1", "title": "星门",
                  "category": "hard_rules", "content": "远古装置"},
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        eid = resp.json()["id"]

        # duplicate title
        resp = self.client.post(
            "/data/worldbook",
            json={"project_id": "p1", "title": "星门"},
        )
        self.assertEqual(resp.status_code, 409)

        items = self.client.get("/data/worldbook?project_id=p1").json()["items"]
        self.assertEqual(len(items), 1)
        self.client.delete(f"/data/worldbook/{eid}")
        self.assertEqual(
            len(self.client.get("/data/worldbook?project_id=p1").json()["items"]),
            0,
        )


class TestStorylineEndpoints(unittest.TestCase):

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.client = _client(self.tmp)

    def test_put_then_get(self) -> None:
        resp = self.client.put(
            "/data/storyline",
            json={"project_id": "p1",
                  "nodes": [
                      {"id": "n1", "title": "起", "chapter_num": 1,
                       "x": 10, "y": 20},
                      {"id": "n2", "title": "承", "chapter_num": 2},
                  ],
                  "edges": [
                      {"id": "e1", "from": "n1", "to": "n2", "label": "导致"},
                  ]},
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        g = self.client.get("/data/storyline?project_id=p1").json()
        self.assertEqual(len(g["nodes"]), 2)
        self.assertEqual(g["edges"][0]["label"], "导致")

    def test_empty_default(self) -> None:
        g = self.client.get("/data/storyline?project_id=unknown").json()
        self.assertEqual(g["nodes"], [])
        self.assertEqual(g["edges"], [])


class TestWritingKnowledgeEndpoints(unittest.TestCase):

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.client = _client(self.tmp)

    def test_crud(self) -> None:
        resp = self.client.post(
            "/data/writing_knowledge",
            json={"title": "节奏", "domain": "pacing",
                  "content": "短句加快节奏"},
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        kid = resp.json()["id"]

        # duplicate
        self.assertEqual(
            self.client.post("/data/writing_knowledge",
                             json={"title": "节奏"}).status_code,
            409,
        )

        # filter by domain
        items = self.client.get(
            "/data/writing_knowledge?domain=pacing"
        ).json()["items"]
        self.assertEqual(len(items), 1)

        self.client.delete(f"/data/writing_knowledge/{kid}")
        self.assertEqual(
            self.client.get(f"/data/writing_knowledge/{kid}").status_code, 404,
        )


class TestChatHistoryEndpoints(unittest.TestCase):

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.client = _client(self.tmp)

    def test_save_load_clear(self) -> None:
        resp = self.client.put(
            "/data/chat_history",
            json={"project_id": "p1", "scope": "pipeline",
                  "messages": [
                      {"role": "user", "content": "hi"},
                      {"role": "assistant", "content": "hello"},
                  ]},
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["count"], 2)

        msgs = self.client.get(
            "/data/chat_history?project_id=p1&scope=pipeline"
        ).json()["messages"]
        self.assertEqual([m["role"] for m in msgs], ["user", "assistant"])

        # different scope is isolated
        self.client.put(
            "/data/chat_history",
            json={"project_id": "p1", "scope": "outline_chat",
                  "messages": [{"role": "user", "content": "B"}]},
        )
        self.assertEqual(
            len(self.client.get(
                "/data/chat_history?project_id=p1&scope=outline_chat"
            ).json()["messages"]),
            1,
        )

        # delete
        self.client.delete(
            "/data/chat_history?project_id=p1&scope=pipeline"
        )
        self.assertEqual(
            self.client.get(
                "/data/chat_history?project_id=p1&scope=pipeline"
            ).json()["messages"],
            [],
        )


class TestProjectsEndpoints(unittest.TestCase):

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.client = _client(self.tmp)

    def test_full_crud(self) -> None:
        # POST
        resp = self.client.post(
            "/data/projects",
            json={"name": "新书", "genre": "玄幻", "platform": "起点"},
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        pid = resp.json()["id"]
        # custom field round-trips
        self.assertEqual(resp.json()["platform"], "起点")

        # GET single
        self.assertEqual(
            self.client.get(f"/data/projects/{pid}").json()["name"], "新书",
        )
        # word_count/chapter_count enriched
        body = self.client.get(f"/data/projects/{pid}").json()
        self.assertIn("word_count", body)

        # PUT update
        self.client.put(
            f"/data/projects/{pid}",
            json={"name": "新书", "genre": "都市", "status": "writing"},
        )
        self.assertEqual(
            self.client.get(f"/data/projects/{pid}").json()["genre"], "都市",
        )

        # LIST
        items = self.client.get("/data/projects").json()["items"]
        self.assertEqual(len(items), 1)

        # DELETE
        self.assertEqual(
            self.client.delete(f"/data/projects/{pid}").status_code, 200,
        )
        self.assertEqual(
            self.client.get(f"/data/projects/{pid}").status_code, 404,
        )


class TestVersionsEndpoints(unittest.TestCase):

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.client = _client(self.tmp)
        # Create a project + chapter so FK works
        proj = self.client.post("/data/projects", json={"name": "P"}).json()
        self.pid = proj["id"]
        from ui.backend.app.services.project_paths import get_db_path
        with sqlite3.connect(get_db_path()) as c:
            c.execute(
                "INSERT INTO chapters (chapter_id, project_id, chapter_num) "
                "VALUES ('ch1', ?, 1)", (self.pid,),
            )
            c.commit()

    def test_post_get_delete(self) -> None:
        resp = self.client.post(
            "/data/versions",
            json={"project_id": self.pid,
                  "version": {"chapter_id": "ch1", "source": "ai",
                              "content": "v1 text"}},
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        vid = resp.json()["version_id"]

        items = self.client.get(
            f"/data/versions?project_id={self.pid}&chapter_id=ch1"
        ).json()["versions"]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["content"], "v1 text")

        self.client.delete(f"/data/versions/{vid}?project_id={self.pid}")
        self.assertEqual(
            self.client.get(
                f"/data/versions?project_id={self.pid}&chapter_id=ch1"
            ).json()["versions"],
            [],
        )

    def test_missing_chapter_id_400(self) -> None:
        resp = self.client.post(
            "/data/versions",
            json={"project_id": self.pid, "version": {"content": "x"}},
        )
        self.assertEqual(resp.status_code, 400)


class TestForeshadowingEndpoints(unittest.TestCase):

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.client = _client(self.tmp)
        self.client.post("/data/projects", json={"id": "p1", "name": "P"})

    def test_get_reads_pending_hooks(self) -> None:
        from ui.backend.app.services.project_paths import get_db_path
        with sqlite3.connect(get_db_path()) as c:
            c.execute(
                "INSERT INTO pending_hooks (hook_id, project_id, description, "
                "status, importance, origin_chapter) "
                "VALUES ('h1', 'p1', '伏笔A', 'open', 'B', 1)"
            )
            c.commit()
        resp = self.client.get("/data/foreshadowing/p1").json()
        self.assertEqual(resp["source"], "pending_hooks")
        self.assertEqual(len(resp["items"]), 1)
        self.assertEqual(resp["items"][0]["content"], "伏笔A")

    def test_put_is_deprecated_noop(self) -> None:
        resp = self.client.put(
            "/data/foreshadowing/p1",
            json={"items": [{"title": "ignored", "content": "x"}]},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json().get("deprecated"))


class TestEditorEndpoints(unittest.TestCase):

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.client = _client(self.tmp)

    def test_put_then_get(self) -> None:
        body = {
            "project_id": "p1",
            "volumes": [{
                "id": "v1", "title": "第一卷", "order": 0,
                "chapters": [{
                    "id": "c1", "title": "第一章", "order": 0,
                    "outline": "大纲", "content": "正文",
                    "synopsis": "梗概", "time": "春",
                    "characters": ["主角"],
                }],
            }],
        }
        resp = self.client.put("/data/editor", json=body)
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertIn("saved_at", resp.json())

        loaded = self.client.get("/data/editor?project_id=p1").json()
        self.assertEqual(len(loaded["volumes"]), 1)
        self.assertEqual(
            loaded["volumes"][0]["chapters"][0]["title"], "第一章"
        )

    def test_chapters_table_populated(self) -> None:
        self.client.put("/data/editor", json={
            "project_id": "p_chk",
            "volumes": [{
                "id": "v1", "order": 0, "chapters": [
                    {"id": "c1", "title": "T1", "order": 0,
                     "content": "abc", "outline": "outline1"},
                ],
            }],
        })
        from ui.backend.app.services.project_paths import get_db_path
        with sqlite3.connect(get_db_path()) as c:
            row = c.execute(
                "SELECT title, outline, final_text FROM chapters "
                "WHERE project_id='p_chk'"
            ).fetchone()
        self.assertEqual(row, ("T1", "outline1", "abc"))


class TestCalibrationEndpoints(unittest.TestCase):

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.client = _client(self.tmp)

    def test_round_trip(self) -> None:
        resp = self.client.put(
            "/data/calibration/p1",
            json={"style_params": {"tone": 30, "pacing": 60}},
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        loaded = self.client.get("/data/calibration/p1").json()
        self.assertEqual(loaded["style_params"]["tone"], 30)
        self.assertEqual(loaded["project_id"], "p1")

    def test_default_on_missing(self) -> None:
        loaded = self.client.get("/data/calibration/unknown").json()
        self.assertEqual(loaded.get("history"), [])
        self.assertEqual(loaded.get("style_params"), {})


class TestInjectionEndpoints(unittest.TestCase):

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.client = _client(self.tmp)

    def test_reference_injection_round_trip(self) -> None:
        self.client.put(
            "/data/reference_injection/p1",
            json={"selections": {"ref_001": ["characters", "plot"]}},
        )
        loaded = self.client.get("/data/reference_injection/p1").json()
        self.assertEqual(loaded["selections"], {"ref_001": ["characters", "plot"]})

    def test_knowledge_injection_round_trip(self) -> None:
        self.client.put(
            "/data/knowledge_injection/p1",
            json={"knowledge_ids": ["wk_1", "wk_2"]},
        )
        loaded = self.client.get("/data/knowledge_injection/p1").json()
        self.assertEqual(loaded["knowledge_ids"], ["wk_1", "wk_2"])


class TestProjectMemoryEndpoints(unittest.TestCase):

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.client = _client(self.tmp)
        from ui.backend.app.services import project_store
        from ui.backend.app.services.project_paths import get_db_path
        project_store.ensure_project_row(get_db_path(), "p1", "t")

    def test_add_get_delete(self) -> None:
        resp = self.client.post(
            "/data/project_memory/p1",
            json={"content": "规则一：xxx", "category": "rule"},
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        mid = resp.json()["id"]

        data = self.client.get("/data/project_memory/p1").json()
        self.assertEqual(len(data["memories"]), 1)
        self.assertEqual(data["memories"][0]["category"], "rule")

        # Replace
        resp = self.client.put(
            "/data/project_memory/p1",
            json={"memories": [
                {"id": "x", "content": "A", "category": "note"},
                {"id": "y", "content": "B", "category": "note"},
            ]},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            len(self.client.get("/data/project_memory/p1").json()["memories"]), 2,
        )

        # Delete single
        self.client.delete("/data/project_memory/p1/x")
        self.assertEqual(
            len(self.client.get("/data/project_memory/p1").json()["memories"]), 1,
        )

    def test_empty_content_400(self) -> None:
        resp = self.client.post(
            "/data/project_memory/p1",
            json={"content": "  "},
        )
        self.assertEqual(resp.status_code, 400)


if __name__ == "__main__":
    unittest.main()
