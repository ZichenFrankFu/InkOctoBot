"""Regression tests for the two editor-save bugs:

1. ``UNIQUE constraint failed: chapters.project_id, chapters.chapter_num``
   when an editor doc had multiple volumes. Per-volume chapter numbering
   collided on the project-wide UNIQUE constraint as soon as Volume 1
   ch.1 met Volume 2 ch.1.

2. Versions saved via /api/data/versions surfaced as empty rows in the
   history panel because the frontend sends ``text`` / ``version`` /
   ``user_edited`` but the legacy backend looked for ``content`` /
   ``version_num`` / ``user_edit``.
"""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from storage.project_schema import ensure_creation_tables
from ui.backend.app.services import project_store


def _fresh_db() -> str:
    db = os.path.join(tempfile.mkdtemp(), "editor_store.db")
    con = sqlite3.connect(db)
    con.execute("PRAGMA foreign_keys=ON")
    ensure_creation_tables(con)
    con.execute("INSERT INTO projects (project_id, title) VALUES ('p1', 't')")
    con.commit()
    con.close()
    return db


def test_save_multi_volume_does_not_collide_on_chap_num() -> None:
    """Two volumes, each with its own chapter 1, used to 500 with
    sqlite3.IntegrityError. Numbering globally must let the save land
    AND produce chapters 1..N across volumes."""
    db = _fresh_db()
    doc = {
        "volumes": [
            {
                "id": "v1", "title": "第一卷", "order": 0,
                "chapters": [
                    {"id": "v1c1", "title": "起", "order": 0, "content": "开端"},
                    {"id": "v1c2", "title": "承", "order": 1, "content": "推进"},
                ],
            },
            {
                "id": "v2", "title": "第二卷", "order": 1,
                "chapters": [
                    # Notice the per-volume `order` resets — this is the
                    # condition that hit the old bug.
                    {"id": "v2c1", "title": "转", "order": 0, "content": "转折"},
                    {"id": "v2c2", "title": "合", "order": 1, "content": "收束"},
                ],
            },
        ],
    }
    project_store.save_editor_doc(db, "p1", doc)

    with sqlite3.connect(db) as c:
        c.row_factory = sqlite3.Row
        rows = c.execute(
            "SELECT chapter_id, volume, chapter_num, title FROM chapters "
            "WHERE project_id='p1' ORDER BY chapter_num"
        ).fetchall()
    # All four chapters land, with globally increasing chap_num.
    assert [r["chapter_num"] for r in rows] == [1, 2, 3, 4]
    assert [r["title"] for r in rows] == ["起", "承", "转", "合"]
    # Volume markers are preserved (v1 → 1, v2 → 2 in document order).
    assert [r["volume"] for r in rows] == [1, 1, 2, 2]
    # chapter_id round-trips from the frontend's `id` field — version
    # history rows attached to v1c1 etc. don't get orphaned.
    assert [r["chapter_id"] for r in rows] == ["v1c1", "v1c2", "v2c1", "v2c2"]


def test_save_handles_stale_chap_num_row() -> None:
    """If a prior buggy save left a row at (project_id, chap_num) with a
    different chapter_id, a fresh save must displace it instead of
    500-ing on the UNIQUE constraint."""
    db = _fresh_db()
    # Simulate the pre-fix state: write a stale row at chap_num=1 with
    # an unrelated chapter_id directly.
    with sqlite3.connect(db) as c:
        c.execute(
            "INSERT INTO chapters (chapter_id, project_id, chapter_num, "
            "volume, title, outline, final_text) "
            "VALUES ('stale_ch', 'p1', 1, 1, 'stale', '', '')"
        )
        c.commit()

    # The user reopens the editor; the doc has a different id for ch 1.
    doc = {
        "volumes": [{
            "id": "v1", "title": "卷", "order": 0,
            "chapters": [
                {"id": "real_ch", "title": "真章", "order": 0, "content": "正文"},
            ],
        }],
    }
    project_store.save_editor_doc(db, "p1", doc)

    with sqlite3.connect(db) as c:
        c.row_factory = sqlite3.Row
        rows = c.execute(
            "SELECT chapter_id, chapter_num, title FROM chapters "
            "WHERE project_id='p1'"
        ).fetchall()
    # Exactly one chapter row, with the new id and original chap_num.
    assert len(rows) == 1
    assert rows[0]["chapter_id"] == "real_ch"
    assert rows[0]["chapter_num"] == 1
    assert rows[0]["title"] == "真章"


def test_insert_version_accepts_frontend_shape() -> None:
    """Saving the TextVersion shape the frontend ships
    ({text, version, source='user_edited'}) must persist the actual text
    and round-trip with both spellings."""
    db = _fresh_db()
    project_store.save_editor_doc(db, "p1", {
        "volumes": [{
            "id": "v1", "title": "卷", "order": 0,
            "chapters": [{"id": "ch1", "title": "ch1", "order": 0, "content": "abc"}],
        }],
    })

    saved = project_store.insert_version(db, "p1", {
        "version_id": "ver1",
        "chapter_id": "ch1",
        "version":     3,                # frontend field name
        "source":      "user_edited",    # frontend label
        "text":        "正文 100 字",     # frontend field name
        "synopsis":    "梗概",
    })
    # Returned payload exposes BOTH spellings so the existing UI keeps
    # working and the backend stays canonical.
    assert saved["text"] == "正文 100 字"
    assert saved["content"] == "正文 100 字"
    assert saved["version"] == 3
    assert saved["version_num"] == 3
    assert saved["source"] == "user_edited"
    assert saved["synopsis"] == "梗概"

    listed = project_store.list_versions(db, "p1", "ch1")
    assert len(listed) == 1
    assert listed[0]["text"] == "正文 100 字"
    assert listed[0]["source"] == "user_edited"


def test_insert_version_maps_auto_saved_to_user_edit_but_preserves_label() -> None:
    """`auto_saved` isn't in the DB CHECK domain; the writer must coerce
    it into a valid value while keeping the original label for the UI."""
    db = _fresh_db()
    project_store.save_editor_doc(db, "p1", {
        "volumes": [{
            "id": "v1", "title": "卷", "order": 0,
            "chapters": [{"id": "ch1", "title": "ch1", "order": 0, "content": "abc"}],
        }],
    })
    saved = project_store.insert_version(db, "p1", {
        "chapter_id": "ch1", "text": "x", "source": "auto_saved",
    })
    assert saved["source"] == "auto_saved"  # preserved for UI

    with sqlite3.connect(db) as c:
        c.row_factory = sqlite3.Row
        row = c.execute("SELECT source FROM text_versions WHERE version_id=?",
                        (saved["version_id"],)).fetchone()
    # DB row uses the CHECK-domain value so it doesn't violate the schema.
    assert row["source"] in ("user_edit", "ai", "rewrite")
