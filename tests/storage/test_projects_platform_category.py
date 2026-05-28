"""Tests for the projects schema platform/category migration
(roadmap Stage 1 Task 5).

Verifies:
  1. Fresh DBs get platform + category columns from `ensure_creation_tables`.
  2. Legacy DBs (table existed without these columns) get them added
     idempotently — old rows preserved.
  3. Loader 2 platform_market reads them via _resolve_project_platform_category.
"""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from storage.project_schema import (
    _ensure_projects_market_columns,
    ensure_creation_tables,
)


def _fresh_db() -> str:
    db = os.path.join(tempfile.mkdtemp(), "pm.db")
    con = sqlite3.connect(db)
    ensure_creation_tables(con)
    con.commit()
    con.close()
    return db


class TestProjectsMarketColumns(unittest.TestCase):

    def test_fresh_db_has_platform_and_category(self) -> None:
        db = _fresh_db()
        with sqlite3.connect(db) as c:
            cols = {r[1] for r in c.execute("PRAGMA table_info(projects)")}
        self.assertIn("platform", cols)
        self.assertIn("category", cols)

    def test_legacy_db_gets_columns_added(self) -> None:
        """Simulate a v1 schema where projects exists without the new
        cols, then run the ensure helper — columns must be added
        without losing old rows."""
        tmpdir = tempfile.mkdtemp()
        db = os.path.join(tmpdir, "legacy.db")
        with sqlite3.connect(db) as c:
            # Minimal legacy projects table.
            c.execute(
                "CREATE TABLE projects ("
                "  project_id TEXT PRIMARY KEY, title TEXT)"
            )
            c.execute(
                "INSERT INTO projects VALUES ('p_old', 'legacy title')"
            )
            c.commit()
            # Verify pre-state.
            cols_before = {r[1] for r in c.execute("PRAGMA table_info(projects)")}
            self.assertNotIn("platform", cols_before)
            self.assertNotIn("category", cols_before)
            # Apply migration.
            _ensure_projects_market_columns(c)
            c.commit()
            # Verify post-state.
            cols_after = {r[1] for r in c.execute("PRAGMA table_info(projects)")}
            self.assertIn("platform", cols_after)
            self.assertIn("category", cols_after)
            # Old row preserved with empty defaults.
            row = c.execute(
                "SELECT project_id, title, platform, category "
                "FROM projects WHERE project_id = 'p_old'"
            ).fetchone()
            self.assertEqual(row, ("p_old", "legacy title", "", ""))

    def test_idempotent(self) -> None:
        """Running the helper twice doesn't fail or duplicate columns."""
        db = _fresh_db()
        with sqlite3.connect(db) as c:
            _ensure_projects_market_columns(c)  # second time
            _ensure_projects_market_columns(c)  # third time
            cols = {r[1] for r in c.execute("PRAGMA table_info(projects)")}
        self.assertIn("platform", cols)
        self.assertIn("category", cols)


class TestLoader2PicksUpColumns(unittest.TestCase):
    """Loader 2 platform_market uses these columns to look up
    platform_profiles. End-to-end: write a project with platform +
    category + a matching profile, verify Loader 2 returns a non-None
    plan."""

    def test_loader_returns_plan_when_columns_populated(self) -> None:
        from unittest import mock
        db = _fresh_db()
        with sqlite3.connect(db) as c:
            c.execute(
                "INSERT INTO projects (project_id, title, platform, category) "
                "VALUES ('p_main', 't', 'qidian', 'xianxia')"
            )
            c.execute(
                "INSERT INTO platform_profiles "
                "(profile_id, platform, category, profile_version, "
                " loader_payload, confidence_label) "
                "VALUES ('pp_x', 'qidian', 'xianxia', 1, "
                " '起点玄幻：复仇流为主。', 'high')"
            )
            c.commit()
        with mock.patch(
            "ui.backend.app.services.project_paths.get_db_path",
            return_value=db,
        ):
            from ui.backend.app.services.prompt_context.loaders import (
                platform_market,
            )
            plan = platform_market.plan("p_main")
        self.assertIsNotNone(plan)
        rendered = plan.render(plan.target)
        self.assertIn("复仇流", rendered)

    def test_loader_returns_none_when_platform_empty(self) -> None:
        from unittest import mock
        db = _fresh_db()
        with sqlite3.connect(db) as c:
            # platform + category both empty → Loader 2 returns None.
            c.execute(
                "INSERT INTO projects (project_id, title) VALUES ('p_blank', 't')"
            )
            c.commit()
        with mock.patch(
            "ui.backend.app.services.project_paths.get_db_path",
            return_value=db,
        ):
            from ui.backend.app.services.prompt_context.loaders import (
                platform_market,
            )
            plan = platform_market.plan("p_blank")
        self.assertIsNone(plan)


if __name__ == "__main__":
    unittest.main()
