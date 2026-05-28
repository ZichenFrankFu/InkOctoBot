"""Tests for builder telemetry diagnostics (LOADER_SPEC v3.1)."""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from storage.project_schema import ensure_creation_tables
from ui.backend.app.services import project_store
from ui.backend.app.services.prompt_context import builder


def _fresh_db() -> str:
    db = os.path.join(tempfile.mkdtemp(), "tel.db")
    con = sqlite3.connect(db)
    ensure_creation_tables(con)
    con.execute("INSERT INTO projects(project_id, title) VALUES ('p1','t')")
    con.commit()
    con.close()
    return db


class _Env:
    def __init__(self, db: str) -> None:
        self._patches = [
            mock.patch(
                "ui.backend.app.services.project_paths.get_db_path",
                return_value=db,
            ),
            mock.patch(
                "ui.backend.app.services.prompt_context.utils.embed_sync",
                return_value=[],
            ),
            mock.patch(
                "ui.backend.app.services.prompt_context.loaders."
                "reader_memory._query_semantic",
                return_value=[],
            ),
        ]

    def __enter__(self):
        for p in self._patches:
            p.start()
        return self

    def __exit__(self, *exc):
        for p in self._patches:
            p.stop()


class TestDiagnostics(unittest.TestCase):

    def test_diagnostics_dict_shape_on_empty_project(self) -> None:
        db = _fresh_db()
        with _Env(db):
            ctx = builder.build_generation_context(
                "p1", chapter_num=1, db_path=db,
            )
        diag = ctx["diagnostics"]
        # MVP shape: total/sections/loaders/alerts.
        self.assertIn("total", diag)
        self.assertIn("chars", diag["total"])
        self.assertIn("tokens", diag["total"])
        self.assertIn("loaders", diag)
        self.assertIn("sections", diag)
        self.assertIn("alerts", diag)
        # Every spec block is listed (active or inactive).
        for k in (
            "platform_directive", "user_preferences",
            "user_special_requirements", "chapter_outline",
            "character_cards", "worldbook", "reference",
            "foreshadowing", "inspiration", "current_chapter_draft",
            "subplots", "skills", "storyland_state", "reader_memory",
        ):
            self.assertIn(k, diag["loaders"], f"missing telemetry for {k}")

    def test_per_loader_fields_present(self) -> None:
        db = _fresh_db()
        with _Env(db):
            ctx = builder.build_generation_context(
                "p1", chapter_num=1, db_path=db,
            )
        for body in ctx["diagnostics"]["loaders"].values():
            for f in ("chars", "tokens", "natural_length",
                      "allocated_budget", "tier", "plan_ms", "render_ms",
                      "status"):
                self.assertIn(f, body)
            self.assertIsInstance(body["plan_ms"], float)
            self.assertIsInstance(body["render_ms"], float)
            self.assertIn(body["status"],
                          ("inactive", "rendered", "clipped"))

    def test_active_loader_records_natural_and_chars(self) -> None:
        db = _fresh_db()
        # Add a character so character_cards becomes active.
        project_store.upsert_character(db, {
            "project_id": "p1", "name": "李星河",
            "personality": "沉稳",
        })
        with _Env(db):
            ctx = builder.build_generation_context(
                "p1", chapter_num=1, characters=["李星河"], db_path=db,
            )
        cc = ctx["diagnostics"]["loaders"]["character_cards"]
        self.assertEqual(cc["status"], "rendered")
        self.assertGreater(cc["natural_length"], 0)
        self.assertGreater(cc["chars"], 0)
        self.assertGreater(cc["tokens"], 0)
        self.assertGreater(cc["allocated_budget"], 0)

    def test_total_tokens_matches_per_block_sum(self) -> None:
        db = _fresh_db()
        project_store.upsert_character(db, {
            "project_id": "p1", "name": "李星河",
        })
        with _Env(db):
            ctx = builder.build_generation_context(
                "p1", chapter_num=1, characters=["李星河"], db_path=db,
            )
        total = ctx["diagnostics"]["total"]["tokens"]
        per_sum = sum(
            v["tokens"] for v in ctx["diagnostics"]["loaders"].values()
        )
        self.assertEqual(total, per_sum)

    def test_inactive_loader_status_is_inactive(self) -> None:
        db = _fresh_db()
        with _Env(db):
            ctx = builder.build_generation_context(
                "p1", chapter_num=1, db_path=db,
            )
        # platform_directive is a placeholder — always inactive.
        self.assertEqual(
            ctx["diagnostics"]["loaders"]["platform_directive"]["status"],
            "inactive",
        )
        self.assertEqual(
            ctx["diagnostics"]["loaders"]["platform_directive"]["chars"], 0,
        )

    def test_exclude_all_drives_inactive(self) -> None:
        db = _fresh_db()
        project_store.upsert_character(db, {
            "project_id": "p1", "name": "X",
        })
        with _Env(db):
            ctx = builder.build_generation_context(
                "p1", chapter_num=1, characters=["X"], db_path=db,
                rag_excludes=["character_cards::__all__"],
            )
        self.assertEqual(
            ctx["diagnostics"]["loaders"]["character_cards"]["status"],
            "inactive",
        )
        self.assertEqual(ctx["blocks"]["character_cards"], "")


if __name__ == "__main__":
    unittest.main()
