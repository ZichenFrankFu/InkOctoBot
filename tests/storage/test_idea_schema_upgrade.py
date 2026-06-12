"""Schema-migration regression for ``ensure_idea_tables``.

Reproduces a real-machine failure: an existing ``idea.db`` from a
pre-v3 install lost its ``project_id`` column, and on backend
startup ``ensure_idea_tables`` tried to create the
``idx_inspirations_project`` index *before* the column-migration
step had run — exploding with ``no such column: project_id`` and
causing every ``/api/references/inspirations`` request to 500."""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))


_LEGACY_DDL = """
CREATE TABLE inspirations (
    id TEXT PRIMARY KEY,
    category TEXT NOT NULL DEFAULT 'other',
    title TEXT,
    content TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""


class TestEnsureIdeaTablesLegacyUpgrade(unittest.TestCase):

    def test_upgrade_from_pre_v3_schema(self) -> None:
        """A legacy DB whose ``inspirations`` table predates the v3
        columns must upgrade cleanly when the new ``ensure_idea_tables``
        runs against it."""
        db = os.path.join(tempfile.mkdtemp(), "idea.db")

        # Seed the legacy shape (no project_id column).
        with sqlite3.connect(db) as con:
            con.executescript(_LEGACY_DDL)
            con.execute(
                "INSERT INTO inspirations (id, category, content) "
                "VALUES ('insp1', 'scene', 'legacy row')"
            )
            con.commit()

        # Run the current ensure_idea_tables — must not raise.
        # ``_ensured_paths`` is a module-level cache that would
        # short-circuit the call on re-runs; clear it for the test.
        from storage import idea_schema
        idea_schema._ensured_paths.clear()
        with sqlite3.connect(db) as con:
            idea_schema.ensure_idea_tables(con)

        # Post-conditions:
        #   - project_id column now present
        #   - idx_inspirations_project index exists
        #   - legacy row still readable
        with sqlite3.connect(db) as con:
            con.row_factory = sqlite3.Row
            cols = {r[1] for r in con.execute(
                "PRAGMA table_info(inspirations)")}
            self.assertIn("project_id", cols)
            self.assertIn("embedding_json", cols)
            idx = {
                r[1] for r in con.execute(
                    "PRAGMA index_list(inspirations)"
                ).fetchall()
            }
            self.assertIn("idx_inspirations_project", idx)
            row = con.execute(
                "SELECT id, content FROM inspirations WHERE id='insp1'"
            ).fetchone()
            self.assertEqual(row["content"], "legacy row")

    def test_fresh_db_still_works(self) -> None:
        """Schema-fresh path is unaffected — empty DB → full v3 shape."""
        db = os.path.join(tempfile.mkdtemp(), "idea_fresh.db")
        from storage import idea_schema
        idea_schema._ensured_paths.clear()
        with sqlite3.connect(db) as con:
            idea_schema.ensure_idea_tables(con)
        with sqlite3.connect(db) as con:
            cols = {r[1] for r in con.execute(
                "PRAGMA table_info(inspirations)")}
            self.assertIn("project_id", cols)


if __name__ == "__main__":
    unittest.main()
