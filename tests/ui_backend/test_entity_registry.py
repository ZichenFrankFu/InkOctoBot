"""Unit tests for ui/backend/app/services/entity_registry.py."""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from storage.project_schema import ensure_creation_tables
from ui.backend.app.services import project_store, entity_registry


def _fresh_db() -> str:
    db = os.path.join(tempfile.mkdtemp(), "er.db")
    con = sqlite3.connect(db)
    con.execute("PRAGMA foreign_keys=ON")
    ensure_creation_tables(con)
    con.execute("INSERT INTO projects (project_id, title) VALUES ('p1','t')")
    con.commit()
    con.close()
    return db


class TestCRUD(unittest.TestCase):

    def setUp(self) -> None:
        self.db = _fresh_db()

    def test_create_get_list_update_delete(self) -> None:
        saved = entity_registry.upsert_entity(self.db, {
            "project_id": "p1", "name": "幽冥谷",
            "entity_type": "location",
            "description": "千年禁地",
            "introduced_chapter": 17,
            "aliases": ["阴谷"],
        })
        self.assertTrue(saved["id"].startswith("ent_"))
        self.assertEqual(saved["entity_type"], "location")
        self.assertEqual(saved["aliases"], ["阴谷"])

        got = entity_registry.get_entity(self.db, saved["id"])
        self.assertEqual(got["name"], "幽冥谷")

        updated = entity_registry.upsert_entity(self.db, {
            **saved, "description": "千年禁地（更新）",
        })
        self.assertEqual(updated["description"], "千年禁地（更新）")

        entity_registry.delete_entity(self.db, saved["id"])
        self.assertIsNone(entity_registry.get_entity(self.db, saved["id"]))

    def test_invalid_type_rejected(self) -> None:
        with self.assertRaises(ValueError):
            entity_registry.upsert_entity(self.db, {
                "project_id": "p1", "name": "X", "entity_type": "monster",
            })

    def test_invalid_source_rejected(self) -> None:
        with self.assertRaises(ValueError):
            entity_registry.upsert_entity(self.db, {
                "project_id": "p1", "name": "X",
                "entity_type": "location", "source": "import",
            })

    def test_unique_name_per_project(self) -> None:
        entity_registry.upsert_entity(self.db, {
            "project_id": "p1", "name": "青云山", "entity_type": "location",
        })
        with self.assertRaises(ValueError):
            entity_registry.upsert_entity(self.db, {
                "project_id": "p1", "name": "青云山", "entity_type": "organization",
            })

    def test_list_filtered_by_type(self) -> None:
        entity_registry.upsert_entity(self.db, {
            "project_id": "p1", "name": "幽冥谷", "entity_type": "location",
        })
        entity_registry.upsert_entity(self.db, {
            "project_id": "p1", "name": "玄阴宗", "entity_type": "organization",
        })
        locs = entity_registry.list_entities(self.db, "p1", entity_type="location")
        orgs = entity_registry.list_entities(self.db, "p1", entity_type="organization")
        self.assertEqual([e["name"] for e in locs], ["幽冥谷"])
        self.assertEqual([e["name"] for e in orgs], ["玄阴宗"])


class TestAutoSync(unittest.TestCase):

    def setUp(self) -> None:
        self.db = _fresh_db()

    def _ent_for_character(self, character_id: str) -> dict | None:
        for e in entity_registry.list_entities(self.db, "p1"):
            if e["source"] == "character" and e["source_ref_id"] == character_id:
                return e
        return None

    def _ent_for_worldbook(self, entry_id: str) -> dict | None:
        for e in entity_registry.list_entities(self.db, "p1"):
            if e["source"] == "worldbook" and e["source_ref_id"] == entry_id:
                return e
        return None

    def test_character_upsert_creates_entity(self) -> None:
        char = project_store.upsert_character(self.db, {
            "project_id": "p1", "name": "李星河",
            "background": "考古少校副官",
        })
        ent = self._ent_for_character(char["id"])
        self.assertIsNotNone(ent)
        self.assertEqual(ent["entity_type"], "character")
        self.assertEqual(ent["name"], "李星河")
        self.assertEqual(ent["description"], "考古少校副官")

    def test_character_update_updates_entity(self) -> None:
        char = project_store.upsert_character(self.db, {
            "project_id": "p1", "name": "李星河",
        })
        project_store.upsert_character(self.db, {
            **char, "name": "李清漪", "background": "改名",
        })
        ent = self._ent_for_character(char["id"])
        self.assertEqual(ent["name"], "李清漪")
        self.assertEqual(ent["description"], "改名")

    def test_character_delete_removes_entity(self) -> None:
        char = project_store.upsert_character(self.db, {
            "project_id": "p1", "name": "李星河",
        })
        self.assertIsNotNone(self._ent_for_character(char["id"]))
        project_store.delete_character(self.db, char["id"])
        self.assertIsNone(self._ent_for_character(char["id"]))

    def test_worldbook_location_syncs_as_location(self) -> None:
        wb = project_store.upsert_worldbook(self.db, {
            "project_id": "p1", "title": "幽冥谷",
            "category": "地点", "content": "千年禁地",
        })
        ent = self._ent_for_worldbook(wb["id"])
        self.assertIsNotNone(ent)
        self.assertEqual(ent["entity_type"], "location")

    def test_worldbook_organization_syncs_as_organization(self) -> None:
        wb = project_store.upsert_worldbook(self.db, {
            "project_id": "p1", "title": "玄阴宗",
            "category": "组织", "content": "对立宗门",
        })
        ent = self._ent_for_worldbook(wb["id"])
        self.assertEqual(ent["entity_type"], "organization")

    def test_worldbook_item_syncs_as_item(self) -> None:
        wb = project_store.upsert_worldbook(self.db, {
            "project_id": "p1", "title": "玉佩",
            "category": "物品", "content": "传家宝",
        })
        ent = self._ent_for_worldbook(wb["id"])
        self.assertEqual(ent["entity_type"], "item")

    def test_worldbook_non_stageable_category_skipped(self) -> None:
        wb = project_store.upsert_worldbook(self.db, {
            "project_id": "p1", "title": "灵根体系",
            "category": "规则", "content": "金木水火土...",
        })
        self.assertIsNone(self._ent_for_worldbook(wb["id"]))

    def test_worldbook_category_change_removes_entity(self) -> None:
        """If a stage-able entry becomes a non-stage-able one, the entity goes away."""
        wb = project_store.upsert_worldbook(self.db, {
            "project_id": "p1", "title": "灵泉",
            "category": "地点", "content": "x",
        })
        self.assertIsNotNone(self._ent_for_worldbook(wb["id"]))
        project_store.upsert_worldbook(self.db, {
            **wb, "category": "规则",
        })
        self.assertIsNone(self._ent_for_worldbook(wb["id"]))

    def test_worldbook_delete_removes_entity(self) -> None:
        wb = project_store.upsert_worldbook(self.db, {
            "project_id": "p1", "title": "幽冥谷", "category": "地点",
        })
        self.assertIsNotNone(self._ent_for_worldbook(wb["id"]))
        project_store.delete_worldbook(self.db, wb["id"])
        self.assertIsNone(self._ent_for_worldbook(wb["id"]))


class TestEntityAPI(unittest.TestCase):

    def setUp(self) -> None:
        self.db = _fresh_db()
        from unittest import mock
        self._patcher = mock.patch(
            "ui.backend.app.routers.entity_api._db", return_value=self.db,
        )
        self._patcher.start()
        from ui.backend.app.routers.entity_api import router
        app = FastAPI()
        app.include_router(router)
        self.client = TestClient(app)

    def tearDown(self) -> None:
        self._patcher.stop()

    def test_create_list_delete(self) -> None:
        res = self.client.post("/api/entities", json={
            "project_id": "p1", "name": "幽冥谷", "entity_type": "location",
        })
        self.assertEqual(res.status_code, 200)
        eid = res.json()["id"]

        res = self.client.get("/api/entities", params={"project_id": "p1"})
        self.assertEqual(len(res.json()["items"]), 1)

        res = self.client.delete(f"/api/entities/{eid}")
        self.assertEqual(res.status_code, 200)

        res = self.client.get(f"/api/entities/{eid}")
        self.assertEqual(res.status_code, 404)

    def test_post_invalid_type_400(self) -> None:
        res = self.client.post("/api/entities", json={
            "project_id": "p1", "name": "x", "entity_type": "wat",
        })
        self.assertEqual(res.status_code, 400)


if __name__ == "__main__":
    unittest.main()
