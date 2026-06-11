"""preference_analyzer — 每 N 章批量偏好学习 + 确认 gate (spec 4.1)."""
from __future__ import annotations

import json
import sqlite3
import uuid
from pathlib import Path

import pytest

from storage.project_schema import ensure_creation_tables
from ui.backend.app.services.commit_pipeline.sub_tasks import (
    SubTaskContext, preference_analyzer,
)
from ui.backend.app.services.style_preferences import (
    load_user_style_preferences,
)


@pytest.fixture
def db(tmp_path: Path) -> str:
    path = tmp_path / "prefs.db"
    conn = sqlite3.connect(str(path))
    ensure_creation_tables(conn)
    conn.execute("INSERT INTO projects(project_id, title) VALUES('p1','x')")
    conn.commit()
    conn.close()
    return str(path)


def _commit_chapter(db: str, num: int, *, draft: str = "", final: str = "",
                    special: str = "") -> str:
    """Insert a chapter + its AI draft / user final versions."""
    cid = f"ch_{num}_{uuid.uuid4().hex[:6]}"
    with sqlite3.connect(db) as con:
        con.execute(
            "INSERT INTO chapters (chapter_id, project_id, chapter_num, "
            "title, special_requirements) VALUES (?, 'p1', ?, ?, ?)",
            (cid, num, f"第{num}章", special),
        )
        vnum = 1
        if draft:
            con.execute(
                "INSERT INTO text_versions (version_id, chapter_id, "
                "version_num, source, content) VALUES (?, ?, ?, 'ai', ?)",
                (f"v_{uuid.uuid4().hex[:8]}", cid, vnum, draft),
            )
            vnum += 1
        if final:
            con.execute(
                "INSERT INTO text_versions (version_id, chapter_id, "
                "version_num, source, content) VALUES (?, ?, ?, 'user_edit', ?)",
                (f"v_{uuid.uuid4().hex[:8]}", cid, vnum, final),
            )
        con.commit()
    return cid


def _ctx(db: str) -> SubTaskContext:
    return SubTaskContext(project_id="p1", chapter_id="", db_path=db)


class TestIntervalGate:
    @pytest.mark.asyncio
    async def test_skips_below_interval(self, db: str) -> None:
        for i in range(1, 4):    # 3 chapters < default 5
            _commit_chapter(db, i, draft="a", final="b")
        out = await preference_analyzer.run(_ctx(db))
        assert out["skipped"] == "interval_not_reached"
        assert out["committed_chapters"] == 3

    @pytest.mark.asyncio
    async def test_runs_at_interval_and_advances_marker(
        self, db: str, monkeypatch,
    ) -> None:
        async def fake_llm(prompt, ctx):
            return {"preferences": [{
                "type": "style", "description": "对话使用短句",
                "source_chapters": [1, 2], "source_type": "edit_inference",
            }]}
        monkeypatch.setattr(preference_analyzer, "_call_llm", fake_llm)
        for i in range(1, 6):    # 5 chapters = default interval
            _commit_chapter(db, i, draft=f"AI第{i}章草稿正文",
                            final=f"用户第{i}章修改后正文")
        out = await preference_analyzer.run(_ctx(db))
        assert out["prefs_emitted"] == 1
        assert out["analyzed_chapters"] == [1, 2, 3, 4, 5]
        # Second run right after: marker advanced → interval not reached.
        out2 = await preference_analyzer.run(_ctx(db))
        assert out2["skipped"] == "interval_not_reached"
        assert out2["last_run_at_count"] == 5

    @pytest.mark.asyncio
    async def test_no_edit_signal_still_advances(self, db: str) -> None:
        same = "完全相同的正文"
        for i in range(1, 6):
            _commit_chapter(db, i, draft=same, final=same)
        out = await preference_analyzer.run(_ctx(db))
        assert out["skipped"] == "no_edit_signal"
        out2 = await preference_analyzer.run(_ctx(db))
        assert out2["skipped"] == "interval_not_reached"


class TestPendingWriteAndGate:
    @pytest.mark.asyncio
    async def test_prefs_land_pending_with_sources(
        self, db: str, monkeypatch,
    ) -> None:
        async def fake_llm(prompt, ctx):
            return {"preferences": [
                {"type": "style", "description": "少用成语",
                 "source_chapters": [3], "source_type": "edit_inference"},
                {"type": "pacing", "description": "开篇直接进入冲突",
                 "source_chapters": [5],
                 "source_type": "special_requirements"},
            ]}
        monkeypatch.setattr(preference_analyzer, "_call_llm", fake_llm)
        for i in range(1, 6):
            _commit_chapter(db, i, draft="x", final="y",
                            special="开篇要快" if i == 5 else "")
        await preference_analyzer.run(_ctx(db))
        with sqlite3.connect(db) as con:
            con.row_factory = sqlite3.Row
            rows = {r["description"]: dict(r) for r in con.execute(
                "SELECT * FROM user_style_preferences WHERE project_id='p1'",
            )}
        assert rows["少用成语"]["is_confirmed"] == 0
        assert json.loads(rows["少用成语"]["source_chapters_json"]) == [3]
        assert rows["开篇直接进入冲突"]["source_type"] == "special_requirements"
        # 机制3: 特殊要求来源的置信度起点高于修改推断
        assert (rows["开篇直接进入冲突"]["confidence"]
                > rows["少用成语"]["confidence"])

    @pytest.mark.asyncio
    async def test_loader_excludes_pending_includes_confirmed(
        self, db: str, monkeypatch,
    ) -> None:
        async def fake_llm(prompt, ctx):
            return {"preferences": [{
                "type": "style", "description": "句子要短",
                "source_chapters": [1], "source_type": "edit_inference",
            }]}
        monkeypatch.setattr(preference_analyzer, "_call_llm", fake_llm)
        for i in range(1, 6):
            _commit_chapter(db, i, draft="x", final="y")
        await preference_analyzer.run(_ctx(db))
        # Pending → not injected (机制4)
        assert load_user_style_preferences("p1", db) == ""
        with sqlite3.connect(db) as con:
            con.execute(
                "UPDATE user_style_preferences SET is_confirmed=1 "
                "WHERE project_id='p1'",
            )
            con.commit()
        block = load_user_style_preferences("p1", db)
        assert "句子要短" in block

    @pytest.mark.asyncio
    async def test_repeat_observation_merges_not_confirms(
        self, db: str, monkeypatch,
    ) -> None:
        async def fake_llm(prompt, ctx):
            return {"preferences": [{
                "type": "style", "description": "对话使用短句",
                "source_chapters": [2], "source_type": "edit_inference",
            }]}
        monkeypatch.setattr(preference_analyzer, "_call_llm", fake_llm)
        with sqlite3.connect(db) as con:
            con.execute(
                "INSERT INTO user_style_preferences (pref_id, project_id, "
                "preference_type, description, confidence, observation_count, "
                "is_confirmed, source_chapters_json) "
                "VALUES ('prf_x', 'p1', 'style', '对话使用短句', 0.2, 1, 0, '[1]')",
            )
            con.commit()
        for i in range(1, 6):
            _commit_chapter(db, i, draft="x", final="y")
        await preference_analyzer.run(_ctx(db))
        with sqlite3.connect(db) as con:
            con.row_factory = sqlite3.Row
            row = con.execute(
                "SELECT * FROM user_style_preferences WHERE pref_id='prf_x'",
            ).fetchone()
        assert row["observation_count"] == 2
        assert row["is_confirmed"] == 0   # never auto-confirms
        assert json.loads(row["source_chapters_json"]) == [1, 2]


class TestNotification:
    @pytest.mark.asyncio
    async def test_notification_created_on_emit(
        self, db: str, monkeypatch,
    ) -> None:
        async def fake_llm(prompt, ctx):
            return {"preferences": [{
                "type": "content", "description": "战斗要有代价",
                "source_chapters": [4], "source_type": "edit_inference",
            }]}
        monkeypatch.setattr(preference_analyzer, "_call_llm", fake_llm)
        for i in range(1, 6):
            _commit_chapter(db, i, draft="x", final="y")
        await preference_analyzer.run(_ctx(db))
        with sqlite3.connect(db) as con:
            row = con.execute(
                "SELECT title, notification_type FROM user_notifications "
                "WHERE project_id='p1'",
            ).fetchone()
        assert row is not None
        assert row[1] == "preference_learning"
        assert "等待确认" in row[0]
