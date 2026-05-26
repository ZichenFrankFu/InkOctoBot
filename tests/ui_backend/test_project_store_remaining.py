"""
Tests for project_store's projects / versions / foreshadowing adapters.

Verifies the last batch of JSON -> DB cutovers from
docs/SCHEMA_REDESIGN.md:
  - projects (data/projects/*.json -> projects table)
  - versions (data/versions/<pid>.json -> text_versions table)
  - foreshadowing legacy reads pending_hooks
"""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from storage.project_schema import ensure_creation_tables
from ui.backend.app.services import project_store


def _fresh_db() -> str:
    db = os.path.join(tempfile.mkdtemp(), "proj_remaining.db")
    con = sqlite3.connect(db)
    con.execute("PRAGMA foreign_keys=ON")
    ensure_creation_tables(con)
    con.close()
    return db


class TestProjects(unittest.TestCase):

    def setUp(self) -> None:
        self.db = _fresh_db()

    def test_crud_round_trip(self) -> None:
        body = {
            "name": "测试项目", "genre": "科幻", "description": "梗概",
            "status": "writing", "platform": "番茄",  # platform is a custom field
        }
        saved = project_store.upsert_project(self.db, body)
        self.assertTrue(saved["id"].startswith("proj_"))
        self.assertEqual(saved["name"], "测试项目")
        self.assertEqual(saved["status"], "writing")
        # Custom field round-trips via style_profile_json.
        self.assertEqual(saved["platform"], "番茄")

        # GET single
        fetched = project_store.get_project(self.db, saved["id"])
        self.assertEqual(fetched["genre"], "科幻")

        # LIST
        items = project_store.list_projects(self.db)
        self.assertEqual(len(items), 1)

        # UPDATE
        updated = project_store.upsert_project(
            self.db, {**saved, "status": "paused"}
        )
        self.assertEqual(updated["status"], "paused")
        self.assertEqual(updated["id"], saved["id"])

        # DELETE
        project_store.delete_project(self.db, saved["id"])
        self.assertIsNone(project_store.get_project(self.db, saved["id"]))

    def test_invalid_status_coerced(self) -> None:
        saved = project_store.upsert_project(self.db, {
            "name": "x", "status": "made_up_status",
        })
        self.assertEqual(saved["status"], "planning")

    def test_delete_cascades_to_characters(self) -> None:
        proj = project_store.upsert_project(self.db, {"name": "P"})
        project_store.upsert_character(self.db, {
            "project_id": proj["id"], "name": "A",
        })
        project_store.delete_project(self.db, proj["id"])
        self.assertEqual(
            project_store.list_characters(self.db, proj["id"]), [],
        )


class TestVersions(unittest.TestCase):

    def setUp(self) -> None:
        self.db = _fresh_db()
        self.proj = project_store.upsert_project(
            self.db, {"name": "P"}
        )
        # Create a chapter row so FK resolves.
        with sqlite3.connect(self.db) as c:
            c.execute(
                """INSERT INTO chapters
                   (chapter_id, project_id, chapter_num, title)
                   VALUES (?, ?, 1, '第一章')""",
                ("ch_x", self.proj["id"]),
            )
            c.commit()

    def test_insert_and_list(self) -> None:
        v1 = project_store.insert_version(self.db, self.proj["id"], {
            "chapter_id": "ch_x", "source": "ai",
            "content": "首版正文", "model_used": "test-model",
        })
        self.assertEqual(v1["version_num"], 1)
        v2 = project_store.insert_version(self.db, self.proj["id"], {
            "chapter_id": "ch_x", "source": "user_edit",
            "content": "用户改后的正文",
        })
        self.assertEqual(v2["version_num"], 2)

        # filtered list (newest first)
        items = project_store.list_versions(
            self.db, self.proj["id"], chapter_id="ch_x",
        )
        self.assertEqual([v["version_num"] for v in items], [2, 1])

        # cross-chapter list
        items_all = project_store.list_versions(self.db, self.proj["id"])
        self.assertEqual(len(items_all), 2)

    def test_chapter_id_required(self) -> None:
        with self.assertRaises(ValueError):
            project_store.insert_version(self.db, self.proj["id"], {})

    def test_invalid_source_coerced(self) -> None:
        v = project_store.insert_version(self.db, self.proj["id"], {
            "chapter_id": "ch_x", "source": "bogus",
        })
        self.assertEqual(v["source"], "ai")

    def test_delete(self) -> None:
        v = project_store.insert_version(self.db, self.proj["id"], {
            "chapter_id": "ch_x",
        })
        project_store.delete_version(self.db, v["version_id"])
        self.assertEqual(
            project_store.list_versions(
                self.db, self.proj["id"], chapter_id="ch_x",
            ),
            [],
        )

    def test_extra_fields_round_trip(self) -> None:
        v = project_store.insert_version(self.db, self.proj["id"], {
            "chapter_id": "ch_x",
            "title": "稿1", "tags": ["v1"],   # extras
        })
        self.assertEqual(v["title"], "稿1")
        self.assertEqual(v["tags"], ["v1"])


class TestForeshadowingLegacy(unittest.TestCase):

    def setUp(self) -> None:
        self.db = _fresh_db()
        self.proj = project_store.upsert_project(self.db, {"name": "P"})

    def test_empty_when_no_hooks(self) -> None:
        self.assertEqual(
            project_store.list_foreshadowing_legacy(self.db, self.proj["id"]),
            [],
        )

    def test_reads_pending_hooks(self) -> None:
        with sqlite3.connect(self.db) as c:
            c.execute(
                "INSERT INTO pending_hooks (hook_id, project_id, description, "
                "status, importance, origin_chapter, expected_payoff_chapter) "
                "VALUES (?, ?, ?, 'open', 'A', 3, 10)",
                ("h1", self.proj["id"], "未揭示的身份"),
            )
            c.execute(
                "INSERT INTO pending_hooks (hook_id, project_id, description, "
                "status, importance, origin_chapter) "
                "VALUES (?, ?, ?, 'resolved', 'B', 1)",
                ("h2", self.proj["id"], "已经回收的"),
            )
            c.commit()
        items = project_store.list_foreshadowing_legacy(self.db, self.proj["id"])
        # Both rows returned (legacy adapter returns ALL hooks; the prompt
        # loader filters to active status).
        self.assertEqual(len(items), 2)
        first = next(it for it in items if it["id"] == "h1")
        self.assertEqual(first["status"], "open")
        self.assertEqual(first["origin_chapter"], 3)
        self.assertEqual(first["expected_payoff_chapter"], 10)
        self.assertEqual(first["content"], "未揭示的身份")


if __name__ == "__main__":
    unittest.main()
