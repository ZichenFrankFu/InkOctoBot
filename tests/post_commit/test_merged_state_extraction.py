"""state_extractor 单次合并抽取 (spec Post-Commit·机制1).

One LLM call must yield Storyland facts + reader-memory L4 events +
hook / subplot node tags; this suite stubs the call and checks every
product lands in its canonical table.
"""
from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path

import pytest

from storage.project_schema import ensure_creation_tables
from ui.backend.app.services.commit_pipeline.sub_tasks import (
    SubTaskContext, state_extractor,
)


@pytest.fixture
def db(tmp_path: Path) -> str:
    path = tmp_path / "merged.db"
    conn = sqlite3.connect(str(path))
    ensure_creation_tables(conn)
    conn.execute("INSERT INTO projects(project_id, title) VALUES('p1','x')")
    conn.execute(
        "INSERT INTO chapters (chapter_id, project_id, chapter_num, title) "
        "VALUES ('ch7', 'p1', 7, '第七章')",
    )
    # One pre-existing open hook + one active subplot for the LLM to reference.
    conn.execute(
        "INSERT INTO pending_hooks (hook_id, project_id, description, "
        "status, importance, scale, origin_chapter) "
        "VALUES ('hk_known', 'p1', '玉佩之谜', 'open', 'B', 'event_clue', 1)",
    )
    conn.execute(
        "INSERT INTO subplot_threads (thread_id, project_id, name, "
        "description, status, thread_type, start_chapter) "
        "VALUES ('th_known', 'p1', '宗门大比', '比武线', 'building', 'sub', 2)",
    )
    conn.commit()
    conn.close()
    return str(path)


def _ctx(db: str) -> SubTaskContext:
    return SubTaskContext(
        project_id="p1", chapter_id="ch7", db_path=db,
        chapter_text="张远在青云山击败了黑衣人，捡到一枚铜钱。", chapter_num=7,
    )


@pytest.mark.asyncio
async def test_single_call_yields_all_three_products(db, monkeypatch) -> None:
    captured: dict = {"calls": 0}

    async def fake_llm(chapter_text, chapter_num, existing, **kw):
        captured["calls"] += 1
        captured["open_hooks"] = kw.get("open_hooks")
        captured["active_subplots"] = kw.get("active_subplots")
        return ({
            "state_deltas": [{
                "subject": "张远", "subject_type": "character",
                "predicate": "位于", "new_value": "青云山",
                "trigger": "战后停留",
            }],
            "ledger_deltas": [], "emotion_deltas": [], "new_entities": [],
            "events": [{
                "event_type": "plot", "description": "张远击败黑衣人",
                "characters_involved": ["张远", "黑衣人"], "importance": "A",
            }],
            "hook_deltas": [
                {"hook_id": "hk_known", "description": "玉佩之谜",
                 "action": "progress"},
                {"hook_id": None, "description": "神秘铜钱的来历",
                 "action": "new", "scale": "boomerang"},
            ],
            "subplot_updates": [
                {"thread_id": "th_known", "name": "宗门大比",
                 "action": "advance"},
            ],
        }, "mock/m")

    monkeypatch.setattr(state_extractor, "_call_llm", fake_llm)
    out = await state_extractor.run(_ctx(db))

    # ONE call covered everything (机制1).
    assert captured["calls"] == 1
    # Open hooks + subplots were offered to the LLM for referencing.
    assert captured["open_hooks"][0]["hook_id"] == "hk_known"
    assert captured["active_subplots"][0]["thread_id"] == "th_known"

    assert out["state_changes"] == 1
    assert out["events"] == 1
    assert out["hook_nodes"] == 2
    assert out["subplot_nodes"] == 1

    with sqlite3.connect(db) as con:
        con.row_factory = sqlite3.Row
        # Storyland SPO fact
        spo = con.execute(
            "SELECT object FROM truth_current_state "
            "WHERE project_id='p1' AND subject='张远' AND predicate='位于' "
            "AND valid_to_chapter IS NULL",
        ).fetchone()
        assert spo["object"] == "青云山"
        # L4 episodic event
        ev = con.execute(
            "SELECT description, importance FROM episodic_events "
            "WHERE project_id='p1' AND chapter_num=7",
        ).fetchone()
        assert ev["description"] == "张远击败黑衣人"
        assert ev["importance"] == 5    # A → 5
        # Existing hook progressed; new boomerang hook created with window.
        known = con.execute(
            "SELECT status, last_advance_chapter FROM pending_hooks "
            "WHERE hook_id='hk_known'",
        ).fetchone()
        assert known["status"] == "progressing"
        assert known["last_advance_chapter"] == 7
        new_hook = con.execute(
            "SELECT scale, expected_payoff_chapter FROM pending_hooks "
            "WHERE description='神秘铜钱的来历'",
        ).fetchone()
        assert new_hook["scale"] == "boomerang"
        assert new_hook["expected_payoff_chapter"] == 10    # 7 + 3
        # Subplot advanced.
        th = con.execute(
            "SELECT status, last_advanced_chapter FROM subplot_threads "
            "WHERE thread_id='th_known'",
        ).fetchone()
        assert th["status"] == "building"
        assert th["last_advanced_chapter"] == 7


@pytest.mark.asyncio
async def test_hallucinated_ids_are_skipped(db, monkeypatch) -> None:
    async def fake_llm(chapter_text, chapter_num, existing, **kw):
        return ({
            "state_deltas": [], "ledger_deltas": [], "emotion_deltas": [],
            "new_entities": [], "events": [],
            "hook_deltas": [
                {"hook_id": "hk_invented", "description": "幻觉伏笔",
                 "action": "resolve"},
            ],
            "subplot_updates": [
                {"thread_id": "th_invented", "name": "幻觉线",
                 "action": "advance"},
            ],
        }, "mock/m")

    monkeypatch.setattr(state_extractor, "_call_llm", fake_llm)
    out = await state_extractor.run(_ctx(db))
    assert out["hook_nodes"] == 0
    assert out["subplot_nodes"] == 0


@pytest.mark.asyncio
async def test_rerun_does_not_duplicate_events(db, monkeypatch) -> None:
    """Retry idempotency: llm_auto L4 rows are replaced, not appended."""
    payloads = [
        {"events": [{"event_type": "plot", "description": "第一次提取",
                     "characters_involved": [], "importance": "B"}]},
        {"events": [{"event_type": "plot", "description": "第二次提取",
                     "characters_involved": [], "importance": "B"}]},
    ]
    calls = {"n": 0}

    async def fake_llm(chapter_text, chapter_num, existing, **kw):
        p = payloads[min(calls["n"], 1)]
        calls["n"] += 1
        return ({
            "state_deltas": [], "ledger_deltas": [], "emotion_deltas": [],
            "new_entities": [], "hook_deltas": [], "subplot_updates": [],
            **p,
        }, "mock/m")

    monkeypatch.setattr(state_extractor, "_call_llm", fake_llm)
    await state_extractor.run(_ctx(db))
    await state_extractor.run(_ctx(db))
    with sqlite3.connect(db) as con:
        rows = con.execute(
            "SELECT description FROM episodic_events "
            "WHERE project_id='p1' AND chapter_num=7 "
            "AND generated_by='llm_auto'",
        ).fetchall()
    assert [r[0] for r in rows] == ["第二次提取"]
