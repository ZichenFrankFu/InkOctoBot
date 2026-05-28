"""Tests for the V2 reference prompt-context loader (LOADER_SPEC Loader 3)."""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from knowledge.reference_db import ReferenceDB
from storage.project_schema import ensure_creation_tables
from ui.backend.app.services import project_store
from ui.backend.app.services.prompt_context.loaders import reference as ref_loader


def _fresh_db() -> str:
    db = os.path.join(tempfile.mkdtemp(), "ref.db")
    con = sqlite3.connect(db)
    con.execute("PRAGMA foreign_keys=ON")
    ensure_creation_tables(con)
    con.execute("INSERT INTO projects (project_id, title) VALUES ('p1','t')")
    con.commit()
    con.close()
    # Build the reference tables in the same DB.
    ReferenceDB(db)
    return db


def _seed_work(db: str, ref_id: str, title: str, *,
               characters: list | None = None,
               settings: dict | None = None,
               plot: dict | None = None) -> None:
    with sqlite3.connect(db) as c:
        c.execute(
            "INSERT INTO reference_works (ref_id, title, media_type, source, "
            "extracted_characters_json, settings_json, plot_outline_json) "
            "VALUES (?, ?, 'web_novel', 'manual', ?, ?, ?)",
            (ref_id, title,
             json.dumps(characters or [], ensure_ascii=False),
             json.dumps(settings or {}, ensure_ascii=False),
             json.dumps(plot or {}, ensure_ascii=False)),
        )
        c.execute(
            "INSERT INTO project_reference_links (link_id, project_id, ref_id, "
            "dimension) VALUES (?, 'p1', ?, 'character')",
            (f"link_{ref_id}", ref_id),
        )
        c.commit()


def _set_injection(db: str, blob: dict) -> None:
    project_store.save_blob(db, "p1", "reference_injection", blob)


class _Env:
    def __init__(self, db: str) -> None:
        self._patcher = mock.patch(
            "ui.backend.app.services.project_paths.get_db_path",
            return_value=db,
        )

    def __enter__(self):
        self._patcher.start()
        return self

    def __exit__(self, *exc):
        self._patcher.stop()


class TestEmptyCases(unittest.TestCase):

    def test_no_works_returns_empty(self) -> None:
        db = _fresh_db()
        with _Env(db):
            self.assertEqual(ref_loader.load("p1", db, "X"), "")

    def test_no_selection_no_explicit_no_auto(self) -> None:
        """With auto_top_k=0 and no explicit picks, output is empty."""
        db = _fresh_db()
        _seed_work(db, "r1", "诡秘之主",
                   characters=[{"name": "克莱恩", "role": "主角"}])
        _set_injection(db, {
            "version": 2,
            "explicit_selections": {},
            "auto_top_k": {"characters": 0, "plot": 0, "worldview": 0,
                            "text_features": 0, "exemplars": 0},
            "min_relevance": 0.3,
        })
        with _Env(db):
            out = ref_loader.load("p1", db, "X")
        self.assertEqual(out, "")


class TestExplicitPath(unittest.TestCase):

    def test_explicit_characters_renders(self) -> None:
        db = _fresh_db()
        _seed_work(db, "r1", "诡秘之主",
                   characters=[
                       {"name": "克莱恩", "role": "主角",
                        "description": "穿越者占卜师"},
                       {"name": "奥黛丽", "role": "配角"},
                   ])
        _set_injection(db, {
            "version": 2,
            "explicit_selections": {"r1": {"characters": True}},
            "auto_top_k": {"characters": 0, "plot": 0, "worldview": 0,
                            "text_features": 0, "exemplars": 0},
            "min_relevance": 0.3,
        })
        with _Env(db):
            out = ref_loader.load("p1", db, "X")
        self.assertIn("《诡秘之主》", out)
        self.assertIn("用户显式选定", out)
        self.assertIn("角色原型", out)
        self.assertIn("克莱恩", out)

    def test_explicit_plot_renders(self) -> None:
        db = _fresh_db()
        _seed_work(db, "r1", "X",
                   plot={"beats": [
                       {"title": "起", "description": "穿越觉醒"},
                       {"title": "承", "description": "组织接触"},
                   ]})
        _set_injection(db, {
            "version": 2,
            "explicit_selections": {"r1": {"plot": True}},
            "auto_top_k": {"characters": 0, "plot": 0, "worldview": 0,
                            "text_features": 0, "exemplars": 0},
            "min_relevance": 0.3,
        })
        with _Env(db):
            out = ref_loader.load("p1", db, "X")
        self.assertIn("剧情结构", out)
        self.assertIn("穿越觉醒", out)


class TestAutoPath(unittest.TestCase):

    def test_auto_fills_quota_when_no_explicit(self) -> None:
        db = _fresh_db()
        _seed_work(db, "r1", "贴题作品",
                   characters=[{"name": "A", "role": "主角", "description": "x"}])
        _seed_work(db, "r2", "不相关作品",
                   characters=[{"name": "B", "role": "配角", "description": "y"}])
        _set_injection(db, {
            "version": 2,
            "explicit_selections": {},
            "auto_top_k": {"characters": 1, "plot": 0, "worldview": 0,
                            "text_features": 0, "exemplars": 0},
            "min_relevance": -1.0,  # no cutoff
        })
        # Outline embedding always returns [1.0]; r1's characters text
        # also embeds to [1.0]; r2 to [0.0]. We mock both calls.
        def fake_embed(texts):
            return [[1.0] if "A" in t or "贴题" in t else [0.0] for t in texts]

        with _Env(db), mock.patch(
            "ui.backend.app.services.prompt_context.loaders.reference.embed_sync",
            side_effect=lambda texts: [[1.0]],
        ), mock.patch(
            "ui.backend.app.services.prompt_context.loaders.reference_features."
            "_common.embed_sync",
            side_effect=fake_embed,
        ):
            out = ref_loader.load("p1", db, "outline 贴题")
        self.assertIn("贴题作品", out)


class TestExclude(unittest.TestCase):

    def test_exclude_drops_work(self) -> None:
        db = _fresh_db()
        _seed_work(db, "r1", "X1",
                   characters=[{"name": "A", "role": "主角", "description": "x"}])
        _set_injection(db, {
            "version": 2,
            "explicit_selections": {"r1": {"characters": True}},
            "auto_top_k": {"characters": 0, "plot": 0, "worldview": 0,
                            "text_features": 0, "exemplars": 0},
            "min_relevance": 0.3,
        })
        with _Env(db):
            out = ref_loader.load("p1", db, "X", exclude={"r1"})
        self.assertEqual(out, "")


class TestV1BlobAutoUpgrade(unittest.TestCase):

    def test_v1_blob_auto_upgrades_for_loader(self) -> None:
        """A V1 blob on disk should drive the loader as if it were V2."""
        db = _fresh_db()
        _seed_work(db, "r1", "X",
                   settings={"power_system": "金木水火土"})
        # V1 shape — `settings` should map to V2 `worldview`.
        _set_injection(db, {
            "selections": {"r1": {"settings": True}},
        })
        with _Env(db):
            out = ref_loader.load("p1", db, "X")
        self.assertIn("世界设定", out)
        self.assertIn("金木水火土", out)


if __name__ == "__main__":
    unittest.main()
