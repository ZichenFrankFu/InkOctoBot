"""Regression for ``replace_chat_history`` cross-scope upsert.

The frontend reuses client-side message ids across multiple chat
scopes (an outline_chat bubble can re-appear in a pipeline scope
during the same generation flow). ``message_id`` is a global
PRIMARY KEY but the function used to DELETE only by
``(project_id, scope)`` and then plain-INSERT, which raised
``UNIQUE constraint failed: chat_messages.message_id`` on the second
scope's PUT and 500'd the request — a frequent crash reported from
the user's live editor session."""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from storage.project_schema import ensure_creation_tables


def _fresh_db() -> str:
    db = os.path.join(tempfile.mkdtemp(), "chat.db")
    con = sqlite3.connect(db)
    ensure_creation_tables(con)
    con.execute(
        "INSERT INTO projects (project_id, title) VALUES ('p1', 'chat-test')"
    )
    con.commit()
    con.close()
    return db


class TestReplaceChatHistoryCrossScope(unittest.TestCase):

    def test_cross_scope_msg_id_reuse_does_not_raise(self) -> None:
        from ui.backend.app.services import project_store
        db = _fresh_db()

        msg = {"id": "shared_msg_1", "role": "user", "content": "hi"}

        # Scope A — first write goes in cleanly.
        project_store.replace_chat_history(db, "p1", "scope_a", [msg])

        # Scope B — same msg.id. Used to raise IntegrityError.
        project_store.replace_chat_history(db, "p1", "scope_b", [msg])

        with sqlite3.connect(db) as con:
            con.row_factory = sqlite3.Row
            rows = con.execute(
                "SELECT * FROM chat_messages WHERE message_id='shared_msg_1'"
            ).fetchall()
        # The row exists exactly once (now in scope_b, per upsert).
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["scope"], "scope_b")

    def test_same_scope_replace_still_works(self) -> None:
        """Plain replace semantics in a single scope are unchanged."""
        from ui.backend.app.services import project_store
        db = _fresh_db()

        project_store.replace_chat_history(
            db, "p1", "scope_a",
            [{"id": "m1", "role": "user", "content": "v1"}],
        )
        # Replace within the same scope: same id, new content.
        project_store.replace_chat_history(
            db, "p1", "scope_a",
            [{"id": "m1", "role": "user", "content": "v2"}],
        )
        with sqlite3.connect(db) as con:
            con.row_factory = sqlite3.Row
            rows = con.execute(
                "SELECT * FROM chat_messages WHERE project_id='p1'"
            ).fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["content"], "v2")


if __name__ == "__main__":
    unittest.main()
