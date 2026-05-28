"""End-to-end: empty project → migrate → apply chapters → query → export."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from storage.project_schema import ensure_creation_tables
from knowledge.storyland_state import migrate as M
from knowledge.storyland_state.markdown_renderer import render_pending_hooks
from knowledge.storyland_state.schemas import (
    ChapterSummaryDelta, EmotionArcEntry, HookDelta, HookImportance,
    NumericalReconciliation, StatePatch, StorylandStateDeltas,
    StorylandStateKind,
)
from knowledge.storyland_state.store import StorylandStateStore


@pytest.fixture
def fresh_db(tmp_path: Path) -> str:
    db = tmp_path / "lifecycle.db"
    conn = sqlite3.connect(str(db))
    ensure_creation_tables(conn)
    conn.execute("INSERT INTO projects(project_id, title) VALUES('demo','x')")
    conn.commit()
    conn.close()
    return str(db)


def test_full_lifecycle(
    tmp_path: Path, fresh_db: str, monkeypatch,
):
    """A simulated 3-chapter writing session against an empty project,
    preceded by a small migration."""
    store = StorylandStateStore("demo", db_path=fresh_db)

    # ── Phase 1: migrate a tiny foreshadowing.json + character_cards ──
    fs_path = tmp_path / "fs.json"
    fs_path.write_text(json.dumps({"items": [
        {"id": "f1", "title": "玉佩之谜",
         "content": "黑衣人的玉佩究竟来自何处",
         "chapter_ids": ["ch1"]},
    ]}, ensure_ascii=False), "utf-8")
    monkeypatch.setattr(M, "_foreshadowing_json_path", lambda _: fs_path)
    monkeypatch.setattr(M, "_character_rows", lambda _: [
        {"name": "张远", "current_location": "青云山",
         "relationships": [{"target_name": "李清漪", "label": "师徒"}]},
        {"name": "李清漪", "current_location": "青云山宗"},
        {"name": "黑衣人", "current_location": "未知"},
    ])
    monkeypatch.setattr(M, "_config_relation_map", lambda: {
        "师徒": {"sentiment": 60, "trust": 75},
    })

    reports = M.migrate_all(
        store, sources=["foreshadowing_json", "character_cards"],
    )
    assert all(r.success for r in reports.values())

    # Existing 玉佩 hook should be present
    hooks = store.query_pending_hooks(status="open")
    assert any(h["description"] == "玉佩之谜" for h in hooks)
    # 3 location triples
    assert len(store.query_current_state(predicate="在")) == 3

    # ── Phase 2: chapter 2 — main 张远 fights, depletes 灵气 ──
    res2 = store.apply_deltas(StorylandStateDeltas(
        chapter_num=2,
        particle_reconciliations=[
            NumericalReconciliation(
                character="张远", resource="灵气",
                old_value=0, operation="set", delta=0, new_value=100,
                reason="初始化", in_text_evidence="灵气满盈",
            ),
        ],
        hook_deltas=[
            HookDelta(hook_id="hX", description="未知敌人", action="new",
                      importance=HookImportance.B,
                      expected_payoff_chapter=7),
        ],
        chapter_summary=ChapterSummaryDelta(
            summary="张远初遇敌人", key_events=["遇袭", "灵气消耗"],
        ),
        emotion_arc_entries=[
            EmotionArcEntry(character="张远", from_state="平静",
                            to_state="警觉", trigger="遇袭"),
        ],
    ), validate=True, known_characters={"张远", "李清漪", "黑衣人"})
    assert res2.success

    # ── Phase 3: chapter 3 — 张远 receives an injury ──
    res3 = store.apply_deltas(StorylandStateDeltas(
        chapter_num=3,
        particle_reconciliations=[
            NumericalReconciliation(
                character="张远", resource="灵气",
                old_value=100, operation="subtract", delta=-40, new_value=60,
                reason="施展剑诀", in_text_evidence="灵气只剩六成",
            ),
        ],
        current_state_patches=[
            StatePatch(subject="张远", predicate="伤势", object="左臂受伤",
                       valid_from_chapter=3),
        ],
        chapter_summary=ChapterSummaryDelta(
            summary="张远负伤", key_events=["伤势"], pov_character="张远",
        ),
    ), validate=True, known_characters={"张远", "李清漪", "黑衣人"})
    assert res3.success

    # ── Phase 4: chapter 7 — gap=5 on hX (importance B, threshold 5) ──
    res7 = store.apply_deltas(StorylandStateDeltas(chapter_num=7))
    assert res7.success
    # hX should now be pressured (last_advance=2, current=7 → gap=5 ≥ 5)
    pressured = store.list_pressured_hooks(current_chapter=7)
    pressured_ids = {h["hook_id"] for h in pressured}
    assert "hX" in pressured_ids

    # ── Phase 5: query checks ──
    # ledger at ch 3 should show 60
    assert store.query_ledger("张远", "灵气", as_of_chapter=3) == 60
    # current_state at ch 3 should include 伤势
    rows = store.query_current_state(subject="张远", chapter_num=3)
    assert any(r["predicate"] == "伤势" for r in rows)
    # chapter_summary at ch 3 exists
    summ = store.query_chapter_summary(3)
    assert summ is not None
    assert "负伤" in summ["summary_text"]

    # ── Phase 6: try a bad ledger delta — gate should reject ──
    bad = store.apply_deltas(
        StorylandStateDeltas(
            chapter_num=8,
            particle_reconciliations=[
                NumericalReconciliation(
                    character="张远", resource="灵气",
                    old_value=99,  # mismatch! current=60
                    operation="subtract", delta=-10, new_value=89,
                    reason="x", in_text_evidence="y",
                ),
            ],
        ),
        validate=True, known_characters={"张远", "李清漪", "黑衣人"},
    )
    assert bad.success is False
    assert any(i.rule_id == "ledger.matches_current"
               for i in bad.cross_ref_issues)
    # ledger unchanged at chapter 3 → 60
    assert store.query_ledger("张远", "灵气") == 60

    # ── Phase 7: Markdown export round-trip ──
    out_dir = tmp_path / "export"
    paths = store.export_markdown(output_dir=str(out_dir))
    assert len(paths) == 6  # character_matrix removed in v3.0
    pending_md = (out_dir / "pending_hooks.md").read_text("utf-8")
    assert "玉佩之谜" in pending_md
    assert "hX" in pending_md  # whether shown by hook_id or referenced
    # frontmatter present
    assert pending_md.startswith("---\n")
    assert "truth_file: pending_hooks" in pending_md

    # ── Phase 8: idempotent re-run of migration ──
    reports2 = M.migrate_all(
        store, sources=["foreshadowing_json"],
    )
    # idempotent_hit should be true for at least one apply
    assert any(
        ar.idempotent_hit
        for r in reports2.values() for ar in r.apply_results
    )


def test_validate_state_after_lifecycle(fresh_db: str, monkeypatch):
    """validate_state should pass on a clean migrated project."""
    from knowledge.storyland_state.validators import validate_state
    store = StorylandStateStore("demo", db_path=fresh_db)
    monkeypatch.setattr(M, "_character_rows", lambda _: [
        {"name": "张远", "current_location": "青云山"},
    ])
    M.migrate_all(store, sources=["character_cards"])
    report = validate_state(store, known_characters={"张远"})
    assert report.passed is True
