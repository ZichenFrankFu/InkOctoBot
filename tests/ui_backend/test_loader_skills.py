"""Tests for the skills prompt-context loader (LOADER_SPEC Loader 14)."""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from storage.project_schema import ensure_creation_tables
from ui.backend.app.services import skill_index
from ui.backend.app.services.prompt_context.loaders import skills as sk_loader


def _fresh_db() -> str:
    db = os.path.join(tempfile.mkdtemp(), "sk.db")
    con = sqlite3.connect(db)
    con.execute("PRAGMA foreign_keys=ON")
    ensure_creation_tables(con)
    con.execute("INSERT INTO projects (project_id, title) VALUES ('p1','t')")
    con.commit()
    con.close()
    skill_index.reset_sync_cache()
    return db


def _seed_skill(db: str, sid: str, desc: str, vec: list[float] | None = None) -> None:
    skill_index.upsert_skill(db, {
        "skill_id": sid, "display_name": sid, "description": desc,
        "kind": "builtin", "body_snippet": f"body of {sid}",
    })
    if vec is not None:
        text = skill_index.skill_embedding_text({
            "display_name": sid, "description": desc, "body_snippet": f"body of {sid}",
        })
        skill_index.set_embedding(db, sid, vec,
                                   skill_index.hash_embedding_text(text))


class _Env:
    def __init__(self, db: str) -> None:
        self._patcher = mock.patch(
            "ui.backend.app.services.project_paths.get_db_path",
            return_value=db,
        )
        # Block the registry sync from clearing the seeded data.
        self._sync_patcher = mock.patch.object(
            skill_index, "_registry_snapshot",
            return_value=[],
        )

    def __enter__(self):
        self._patcher.start()
        self._sync_patcher.start()
        # Mark already-synced so sync_from_registry is a no-op.
        skill_index.reset_sync_cache()
        # Pre-fill cache by calling once with an empty registry... but
        # that would wipe seeded data. Instead, set the cache directly.
        skill_index._synced_paths.add(self._patcher.kwargs.get("return_value") or "")
        return self

    def __exit__(self, *exc):
        self._patcher.stop()
        self._sync_patcher.stop()


class TestSkillsLoader(unittest.TestCase):

    def setUp(self) -> None:
        self.db = _fresh_db()
        # Pre-empt the lazy registry-sync inside the loader: pretend
        # we've already synced this path.
        skill_index._synced_paths.add(self.db)

    def tearDown(self) -> None:
        skill_index.reset_sync_cache()

    def _patch(self):
        return mock.patch(
            "ui.backend.app.services.project_paths.get_db_path",
            return_value=self.db,
        )

    def test_empty_returns_empty(self) -> None:
        with self._patch():
            self.assertEqual(sk_loader.load("p1", "X"), "")

    def test_user_pinned_param_force_includes(self) -> None:
        _seed_skill(self.db, "k1", "技能 1", [1.0, 0.0])
        _seed_skill(self.db, "k2", "技能 2", [0.0, 1.0])
        with self._patch(), mock.patch.object(
            sk_loader, "embed_sync", return_value=[[1.0, 0.0]],
        ):
            out = sk_loader.load(
                "p1", "X",
                user_pinned_skill_ids=["k2"],
                min_relevance=0.99,
                max_total=3,
            )
        self.assertIn("用户主动选中", out)
        self.assertIn("k2", out)
        # k1 would normally be the top similarity match, but
        # min_relevance=0.99 lets only the pinned one through.

    def test_project_pin_also_force_includes(self) -> None:
        _seed_skill(self.db, "kP", "项目级 pin", [0.5, 0.5])
        skill_index.pin_skill(self.db, "p1", "kP")
        with self._patch(), mock.patch.object(
            sk_loader, "embed_sync", return_value=[[1.0, 0.0]],
        ):
            out = sk_loader.load("p1", "X", min_relevance=0.99)
        self.assertIn("kP", out)

    def test_auto_ranked_by_cosine(self) -> None:
        _seed_skill(self.db, "good", "贴题", [1.0, 0.0])
        _seed_skill(self.db, "bad",  "不贴题", [0.0, 1.0])
        _seed_skill(self.db, "mid",  "中等", [0.7, 0.7])
        with self._patch(), mock.patch.object(
            sk_loader, "embed_sync", return_value=[[1.0, 0.0]],
        ):
            out = sk_loader.load("p1", "X", max_total=2, min_relevance=0.0)
        self.assertIn("系统推荐", out)
        self.assertIn("good", out)
        self.assertIn("mid", out)
        self.assertNotIn("bad", out)

    def test_min_relevance_cuts_off(self) -> None:
        _seed_skill(self.db, "k1", "x", [1.0, 0.0])
        _seed_skill(self.db, "k2", "y", [0.0, 1.0])
        with self._patch(), mock.patch.object(
            sk_loader, "embed_sync", return_value=[[1.0, 0.0]],
        ):
            out = sk_loader.load("p1", "X", max_total=2, min_relevance=0.99)
        self.assertIn("k1", out)
        self.assertNotIn("k2", out)

    def test_embedding_failure_falls_back_to_alphabetical(self) -> None:
        _seed_skill(self.db, "alpha", "x")
        _seed_skill(self.db, "beta", "y")
        with self._patch(), mock.patch.object(
            sk_loader, "embed_sync", return_value=[[]],
        ):
            out = sk_loader.load("p1", "X", max_total=2, min_relevance=0.0)
        self.assertIn("alpha", out)
        self.assertIn("beta", out)

    def test_exclude_drops_skill(self) -> None:
        _seed_skill(self.db, "drop_me", "x", [1.0, 0.0])
        _seed_skill(self.db, "keep_me", "y", [1.0, 0.0])
        with self._patch(), mock.patch.object(
            sk_loader, "embed_sync", return_value=[[1.0, 0.0]],
        ):
            out = sk_loader.load("p1", "X", exclude={"drop_me"},
                                  max_total=3, min_relevance=0.0)
        self.assertNotIn("drop_me", out)
        self.assertIn("keep_me", out)


if __name__ == "__main__":
    unittest.main()
