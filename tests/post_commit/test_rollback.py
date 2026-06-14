"""事务性回溯 (spec 回溯·机制2/机制3) — all four state groups roll
back together; a failure rolls back NOTHING."""
from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path

import pytest

from storage.project_schema import ensure_creation_tables
from ui.backend.app.services.rollback import (
    preview_rollback, rollback_to_chapter,
)


@pytest.fixture
def db(tmp_path: Path) -> str:
    """A project with state across chapters 1-3 (rollback target = 1)."""
    path = tmp_path / "rb.db"
    con = sqlite3.connect(str(path))
    ensure_creation_tables(con)
    con.execute("INSERT INTO projects(project_id, title) VALUES('p1','x')")
    for num in (1, 2, 3):
        con.execute(
            "INSERT INTO chapters (chapter_id, project_id, chapter_num, title) "
            "VALUES (?, 'p1', ?, ?)",
            (f"ch{num}", num, f"第{num}章"),
        )
        con.execute(
            "INSERT INTO text_versions (version_id, chapter_id, version_num, "
            "source, content) VALUES (?, ?, 1, 'ai', ?)",
            (f"v{num}", f"ch{num}", f"第{num}章正文"),
        )
        con.execute(
            "INSERT INTO chapter_summaries (summary_id, project_id, "
            "chapter_num, summary_text) VALUES (?, 'p1', ?, ?)",
            (f"sum{num}", num, f"第{num}章摘要"),
        )
        con.execute(
            "INSERT INTO episodic_events (event_id, project_id, chapter_num, "
            "event_type, description, importance) "
            "VALUES (?, 'p1', ?, 'plot', ?, 3)",
            (f"ev{num}", num, f"第{num}章事件"),
        )
        con.execute(
            "INSERT INTO character_ledger (ledger_id, project_id, "
            "character_name, chapter_num, category, key, operation, delta) "
            "VALUES (?, 'p1', '张远', ?, 'resource', '灵气', 'add', 10)",
            (f"lg{num}", num),
        )
        con.execute(
            "INSERT INTO emotion_arcs (arc_id, project_id, character_name, "
            "chapter_num, from_state, to_state) "
            "VALUES (?, 'p1', '张远', ?, '平静', '愤怒')",
            (f"ea{num}", num),
        )
    # SPO: fact opened ch1, superseded at ch2 by a new fact.
    con.execute(
        "INSERT INTO truth_current_state (triple_id, project_id, subject, "
        "subject_type, predicate, object, valid_from_chapter, "
        "valid_to_chapter, superseded_by) "
        "VALUES ('t_old', 'p1', '张远', 'character', '位于', '青云山', 1, 1, 't_new')",
    )
    con.execute(
        "INSERT INTO truth_current_state (triple_id, project_id, subject, "
        "subject_type, predicate, object, valid_from_chapter) "
        "VALUES ('t_new', 'p1', '张远', 'character', '位于', '黑风寨', 2)",
    )
    # Hooks: one from ch1 (progressed at ch3), one originated at ch2.
    con.execute(
        "INSERT INTO pending_hooks (hook_id, project_id, description, "
        "status, importance, scale, origin_chapter, last_mention_chapter, "
        "last_advance_chapter) "
        "VALUES ('hk1', 'p1', '玉佩之谜', 'progressing', 'B', 'event_clue', 1, 3, 3)",
    )
    for ch, action, before, after in (
        (1, "new", None, "open"), (3, "progress", "open", "progressing"),
    ):
        con.execute(
            "INSERT INTO hook_events (event_id, hook_id, project_id, "
            "chapter_num, action, before_status, after_status) "
            "VALUES (?, 'hk1', 'p1', ?, ?, ?, ?)",
            (f"he_{uuid.uuid4().hex[:6]}", ch, action, before, after),
        )
    con.execute(
        "INSERT INTO pending_hooks (hook_id, project_id, description, "
        "status, importance, scale, origin_chapter) "
        "VALUES ('hk2', 'p1', '第二章新伏笔', 'open', 'B', 'boomerang', 2)",
    )
    # Subplots: one started ch1 advanced at ch3, one started ch3.
    con.execute(
        "INSERT INTO subplot_threads (thread_id, project_id, name, status, "
        "thread_type, start_chapter, last_advanced_chapter) "
        "VALUES ('th1', 'p1', '宗门大比', 'building', 'sub', 1, 3)",
    )
    con.execute(
        "INSERT INTO subplot_threads (thread_id, project_id, name, status, "
        "thread_type, start_chapter) "
        "VALUES ('th3', 'p1', '第三章新线', 'setup', 'sub', 3)",
    )
    con.commit()
    con.close()
    return str(path)


class TestPreview:
    def test_preview_counts(self, db: str) -> None:
        p = preview_rollback(db, "p1", 1)
        assert p["text_versions"] == 2          # ch2, ch3
        assert p["chapter_summaries"] == 2
        assert p["episodic_events"] == 2
        assert p["ledger_entries"] == 2
        assert p["emotion_arcs"] == 2
        assert p["hooks"] == 1                  # hk2 (origin ch2)
        assert p["hook_events"] == 1            # hk1's ch3 progress
        assert p["subplot_threads"] == 1        # th3
        assert p["spo_deleted"] == 1            # t_new (from ch2)
        assert p["spo_reopened"] == 1           # t_old (closed at ch1)

    def test_preview_is_read_only(self, db: str) -> None:
        preview_rollback(db, "p1", 1)
        with sqlite3.connect(db) as con:
            n = con.execute("SELECT COUNT(*) FROM text_versions").fetchone()[0]
        assert n == 3


class TestExecute:
    def test_all_four_groups_roll_back_together(self, db: str) -> None:
        out = rollback_to_chapter(db, "p1", 1)
        with sqlite3.connect(db) as con:
            con.row_factory = sqlite3.Row
            # 1. 章节正文 — only chapter 1's version survives.
            vids = [r[0] for r in con.execute(
                "SELECT version_id FROM text_versions ORDER BY version_id")]
            assert vids == ["v1"]
            # 2. Storyland — ch2 fact gone, ch1 fact reopened.
            assert con.execute(
                "SELECT COUNT(*) FROM truth_current_state "
                "WHERE triple_id='t_new'").fetchone()[0] == 0
            old = con.execute(
                "SELECT valid_to_chapter, superseded_by "
                "FROM truth_current_state WHERE triple_id='t_old'").fetchone()
            assert old["valid_to_chapter"] is None
            assert old["superseded_by"] is None
            assert con.execute(
                "SELECT COUNT(*) FROM character_ledger "
                "WHERE chapter_num > 1").fetchone()[0] == 0
            # 3. 读者记忆 — L2/L4 beyond ch1 gone.
            assert con.execute(
                "SELECT COUNT(*) FROM chapter_summaries "
                "WHERE chapter_num > 1").fetchone()[0] == 0
            assert con.execute(
                "SELECT COUNT(*) FROM episodic_events "
                "WHERE chapter_num > 1").fetchone()[0] == 0
            # 4. 伏笔/故事线 — hk2 gone; hk1 rewound to its ch1 state.
            assert con.execute(
                "SELECT COUNT(*) FROM pending_hooks "
                "WHERE hook_id='hk2'").fetchone()[0] == 0
            hk1 = con.execute(
                "SELECT status, last_mention_chapter, last_advance_chapter "
                "FROM pending_hooks WHERE hook_id='hk1'").fetchone()
            assert hk1["status"] == "open"
            assert hk1["last_mention_chapter"] in (None, 1)
            assert hk1["last_advance_chapter"] == 1
            assert con.execute(
                "SELECT COUNT(*) FROM subplot_threads "
                "WHERE thread_id='th3'").fetchone()[0] == 0
            th1 = con.execute(
                "SELECT last_advanced_chapter FROM subplot_threads "
                "WHERE thread_id='th1'").fetchone()
            assert th1["last_advanced_chapter"] == 1
        assert out["text_versions"] == 2
        assert out["hooks"] == 1

    def test_failure_rolls_back_nothing(self, db: str, monkeypatch) -> None:
        """机制3: rollback is one transaction — induced failure midway
        must leave every table untouched."""
        import ui.backend.app.services.rollback as rb

        def boom(con, project_id, target):
            raise RuntimeError("induced failure after deletes began")
        monkeypatch.setattr(rb, "_rewind_surviving_hooks", boom)
        with pytest.raises(RuntimeError):
            rollback_to_chapter(db, "p1", 1)
        with sqlite3.connect(db) as con:
            # Earlier deletes in the same transaction were rolled back.
            assert con.execute(
                "SELECT COUNT(*) FROM text_versions").fetchone()[0] == 3
            assert con.execute(
                "SELECT COUNT(*) FROM pending_hooks").fetchone()[0] == 2
            assert con.execute(
                "SELECT COUNT(*) FROM truth_current_state").fetchone()[0] == 2

    def test_rollback_to_zero_clears_everything(self, db: str) -> None:
        rollback_to_chapter(db, "p1", 0)
        with sqlite3.connect(db) as con:
            for table in ("text_versions", "chapter_summaries",
                          "episodic_events", "pending_hooks",
                          "subplot_threads"):
                assert con.execute(
                    f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0, table
