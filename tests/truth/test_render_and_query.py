"""Tests for C4: query API + Markdown renderer + bundle/export."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from storage.creation_schema import ensure_creation_tables
from knowledge.truth.markdown_renderer import (
    render, render_current_state, render_particle_ledger,
    render_pending_hooks, render_chapter_summaries,
    render_subplot_board, render_emotional_arcs,
    render_character_matrix,
)
from knowledge.truth.schemas import (
    ChapterSummaryDelta, EmotionArcEntry, HookDelta, HookImportance,
    NumericalReconciliation, RelationUpdate, StatePatch, SubplotStatus,
    SubplotUpdate, TruthDeltas, TruthFileKind,
)
from knowledge.truth.store import TruthFileStore


# ─────── fixtures ───────

@pytest.fixture
def db_path(tmp_path: Path) -> str:
    db = tmp_path / "t.db"
    conn = sqlite3.connect(str(db))
    ensure_creation_tables(conn)
    conn.execute("INSERT INTO projects(project_id, title) VALUES('p1','x')")
    conn.commit()
    conn.close()
    return str(db)


@pytest.fixture
def seeded_store(db_path: str) -> TruthFileStore:
    store = TruthFileStore("p1", db_path=db_path)
    store.apply_deltas(TruthDeltas(
        chapter_num=5,
        current_state_patches=[
            StatePatch(subject="张远", predicate="在", object="青云山",
                       valid_from_chapter=5),
            StatePatch(subject="张远", predicate="情绪", object="平静",
                       valid_from_chapter=5),
        ],
        particle_reconciliations=[
            NumericalReconciliation(
                character="张远", resource="灵气",
                old_value=0, operation="set", delta=0, new_value=100,
                reason="初始化", in_text_evidence="ok",
            ),
        ],
        hook_deltas=[
            HookDelta(hook_id="h_玉佩", description="玉佩之谜",
                      action="new", importance=HookImportance.A,
                      expected_payoff_chapter=10),
        ],
        chapter_summary=ChapterSummaryDelta(
            summary="主角下山", key_events=["拾玉佩", "遭遇袭击"],
            pov_character="张远",
        ),
        subplot_updates=[
            SubplotUpdate(thread_id="sp1", name="主角下山线", action="new",
                          status_after=SubplotStatus.setup,
                          related_hook_ids=["h_玉佩"]),
        ],
        emotion_arc_entries=[
            EmotionArcEntry(character="张远", from_state="平静",
                            to_state="警觉", trigger="袭击"),
        ],
        character_relation_updates=[
            RelationUpdate(character_a="张远", character_b="李清漪",
                           relation_type="师徒", sentiment_score=60,
                           trust_level=70),
        ],
    ))
    return store


# ─────── query: current_state ───────

class TestCurrentStateQuery:

    def test_query_all(self, seeded_store: TruthFileStore):
        rows = seeded_store.query_current_state()
        assert len(rows) == 2

    def test_query_by_subject(self, seeded_store: TruthFileStore):
        rows = seeded_store.query_current_state(subject="张远")
        assert all(r["subject"] == "张远" for r in rows)

    def test_query_chapter_window(self, seeded_store: TruthFileStore):
        # state added at ch 5; ch 4 → no row (window starts at 5)
        rows4 = seeded_store.query_current_state(chapter_num=4)
        rows5 = seeded_store.query_current_state(chapter_num=5)
        assert rows4 == []
        assert len(rows5) == 2

    def test_query_after_supersede(self, db_path: str, seeded_store: TruthFileStore):
        # add a newer triple for (张远, 在) at ch 8 — old should be closed
        seeded_store.apply_deltas(TruthDeltas(
            chapter_num=8,
            current_state_patches=[
                StatePatch(subject="张远", predicate="在", object="幽冥谷",
                           valid_from_chapter=8),
            ],
        ))
        # ch 5: old triple should NOT be in window because superseded at 7
        rows5 = seeded_store.query_current_state(
            subject="张远", predicate="在", chapter_num=5,
        )
        assert len(rows5) == 1
        assert rows5[0]["object"] == "青云山"
        # ch 8: only new triple valid
        rows8 = seeded_store.query_current_state(
            subject="张远", predicate="在", chapter_num=8,
        )
        assert len(rows8) == 1
        assert rows8[0]["object"] == "幽冥谷"


# ─────── query: ledger ───────

class TestLedgerQuery:

    def test_current_value(self, seeded_store: TruthFileStore):
        assert seeded_store.query_ledger("张远", "灵气") == 100

    def test_current_value_unknown_returns_none(self, seeded_store: TruthFileStore):
        assert seeded_store.query_ledger("unknown", "灵气") is None

    def test_as_of_chapter_filters_history(self, seeded_store: TruthFileStore):
        # at ch 5 灵气=100; add ch 8 灵气=70
        seeded_store.apply_deltas(TruthDeltas(
            chapter_num=8,
            particle_reconciliations=[
                NumericalReconciliation(
                    character="张远", resource="灵气",
                    old_value=100, operation="subtract", delta=-30, new_value=70,
                    reason="x", in_text_evidence="y",
                ),
            ],
        ))
        assert seeded_store.query_ledger("张远", "灵气", as_of_chapter=5) == 100
        assert seeded_store.query_ledger("张远", "灵气", as_of_chapter=8) == 70

    def test_list_ledger_entries(self, seeded_store: TruthFileStore):
        rows = seeded_store.list_ledger_entries(character="张远")
        assert len(rows) == 1
        assert rows[0]["new_value"] == 100


# ─────── query: pending_hooks ───────

class TestHookQuery:

    def test_query_by_status(self, seeded_store: TruthFileStore):
        rows = seeded_store.query_pending_hooks(status="open")
        assert len(rows) == 1
        assert rows[0]["hook_id"] == "h_玉佩"

    def test_list_pressured_when_overdue(self, db_path: str):
        store = TruthFileStore("p1", db_path=db_path)
        # importance B, threshold 5
        store.apply_deltas(TruthDeltas(
            chapter_num=1,
            hook_deltas=[HookDelta(hook_id="hB", description="x",
                                   action="new", importance=HookImportance.B)],
        ))
        # advance past threshold → pressured
        store.apply_deltas(TruthDeltas(chapter_num=7))
        # pressed: status='pressured' set by _recompute_pressure
        pressured = store.list_pressured_hooks(current_chapter=7)
        assert any(h["hook_id"] == "hB" for h in pressured)

    def test_list_pressured_not_overdue(self, seeded_store: TruthFileStore):
        # h_玉佩 has importance A → threshold 3; current=5 last_advance=5
        # gap = 0 → not pressured
        assert seeded_store.list_pressured_hooks(current_chapter=5) == []


# ─────── query: chapter summary ───────

def test_query_chapter_summary(seeded_store: TruthFileStore):
    summ = seeded_store.query_chapter_summary(chapter_num=5)
    assert summ is not None
    assert summ["summary_text"] == "主角下山"


def test_query_chapter_summary_missing(seeded_store: TruthFileStore):
    assert seeded_store.query_chapter_summary(chapter_num=999) is None


def test_list_chapter_summaries(seeded_store: TruthFileStore):
    rows = seeded_store.list_chapter_summaries()
    assert len(rows) == 1
    assert rows[0]["chapter_num"] == 5


# ─────── query: subplots, emotions, relations ───────

def test_query_subplot_threads(seeded_store: TruthFileStore):
    rows = seeded_store.query_subplot_threads()
    assert len(rows) == 1
    assert rows[0]["thread_id"] == "sp1"
    assert rows[0]["status"] == "setup"


def test_query_emotion_arc(seeded_store: TruthFileStore):
    rows = seeded_store.query_emotion_arc(character="张远")
    assert len(rows) == 1


def test_query_character_matrix_by_character(seeded_store: TruthFileStore):
    rows = seeded_store.query_character_matrix(character="李清漪")
    assert len(rows) == 1
    assert rows[0]["character_a"] == "张远"
    assert rows[0]["character_b"] == "李清漪"


# ─────── markdown renderers ───────

class TestRenderers:

    def test_current_state_renders_frontmatter(self):
        out = render_current_state(
            [], project_id="p1", chapter_pointer=5, budget_chars=2000,
        )
        assert out.startswith("---\n")
        assert "project_id: p1" in out
        assert "truth_file: current_state" in out
        assert "_(empty)_" in out

    def test_current_state_renders_triples(self):
        triples = [
            {"subject": "张远", "predicate": "在",
             "object": "青云山", "valid_from_chapter": 5,
             "valid_to_chapter": None, "superseded_by": None,
             "confidence": 1.0},
        ]
        out = render_current_state(
            triples, project_id="p1", chapter_pointer=5, budget_chars=2000,
        )
        assert "张远" in out and "青云山" in out
        assert "Ch 5 – present" in out

    def test_particle_ledger_renders_table(self):
        entries = [
            {"character_name": "张远", "chapter_num": 5,
             "key": "灵气", "operation": "subtract",
             "delta": -30, "new_value": 50, "reason": "施展剑诀"},
        ]
        out = render_particle_ledger(
            entries, project_id="p1", chapter_pointer=5, budget_chars=2000,
        )
        assert "| Chapter |" in out  # table header
        assert "灵气" in out and "施展剑诀" in out

    def test_pending_hooks_groups_by_status(self):
        hooks = [
            {"hook_id": "h1", "description": "a", "status": "pressured",
             "importance": "A", "origin_chapter": 3,
             "last_advance_chapter": 5, "expected_payoff_chapter": 10,
             "pressure_threshold": 3},
            {"hook_id": "h2", "description": "b", "status": "open",
             "importance": "B", "origin_chapter": 5,
             "last_advance_chapter": 5, "expected_payoff_chapter": None,
             "pressure_threshold": 5},
        ]
        out = render_pending_hooks(
            hooks, project_id="p1", chapter_pointer=8, budget_chars=3000,
        )
        assert "[Pressured]" in out
        assert "[Open]" in out
        # pressured comes before open in output
        assert out.index("[Pressured]") < out.index("[Open]")

    def test_chapter_summaries_handles_json_string(self):
        summaries = [
            {"chapter_num": 5, "summary_text": "下山",
             "key_events": '["拾玉佩","遭遇袭击"]'},
        ]
        out = render_chapter_summaries(
            summaries, project_id="p1", chapter_pointer=5, budget_chars=3000,
        )
        assert "拾玉佩" in out and "遭遇袭击" in out

    def test_subplot_board_status_ordering(self):
        threads = [
            {"thread_id": "s1", "name": "setup-line", "status": "setup",
             "start_chapter": 1, "related_hook_ids": "[]"},
            {"thread_id": "s2", "name": "climax-line", "status": "climax",
             "start_chapter": 2, "related_hook_ids": "[]"},
        ]
        out = render_subplot_board(
            threads, project_id="p1", chapter_pointer=5, budget_chars=2000,
        )
        # climax appears before setup
        assert out.index("Climax") < out.index("Setup")

    def test_emotional_arcs_grouped_by_character(self):
        arcs = [
            {"character_name": "张远", "chapter_num": 5,
             "from_state": "平静", "to_state": "警觉", "trigger": "袭击"},
        ]
        out = render_emotional_arcs(
            arcs, project_id="p1", chapter_pointer=5, budget_chars=2000,
        )
        assert "张远" in out and "平静 → 警觉" in out

    def test_character_matrix_table(self):
        rels = [
            {"character_a": "张远", "character_b": "李清漪",
             "relation_type": "师徒", "sentiment_score": 60,
             "trust_level": 70, "last_interaction_chapter": 5},
        ]
        out = render_character_matrix(
            rels, project_id="p1", chapter_pointer=5, budget_chars=2000,
        )
        assert "| Other |" in out
        assert "+60" in out  # signed sentiment
        assert "Ch 5" in out

    def test_dispatch_round_trip(self):
        for kind in TruthFileKind:
            out = render(kind, [], project_id="p1",
                         chapter_pointer=1, budget_chars=1000)
            assert "---\n" in out and "_(empty)_" in out


# ─────── store render integration ───────

class TestStoreRender:

    def test_render_for_prompt_returns_markdown(
        self, seeded_store: TruthFileStore,
    ):
        out = seeded_store.render_for_prompt(
            TruthFileKind.pending_hooks, chapter_num=5,
        )
        assert "Pending Hooks" in out
        assert "h_玉佩" in out

    def test_render_bundle_default_kinds(self, seeded_store: TruthFileStore):
        bundle = seeded_store.render_bundle_for_prompt(
            chapter_num=5, characters=["张远", "李清漪"],
        )
        # all 7 kinds returned
        assert len(bundle) == 7
        for k, md in bundle.items():
            assert "---\n" in md, f"frontmatter missing for {k}"

    def test_render_bundle_with_kinds_filter(self, seeded_store: TruthFileStore):
        bundle = seeded_store.render_bundle_for_prompt(
            chapter_num=5,
            kinds=[TruthFileKind.pending_hooks, TruthFileKind.particle_ledger],
        )
        assert set(bundle.keys()) == {
            TruthFileKind.pending_hooks, TruthFileKind.particle_ledger,
        }

    def test_export_markdown_writes_files(
        self, seeded_store: TruthFileStore, tmp_path: Path,
    ):
        out_dir = tmp_path / "export"
        paths = seeded_store.export_markdown(output_dir=str(out_dir))
        assert len(paths) == 7
        for k, p in paths.items():
            assert p.exists()
            text = p.read_text(encoding="utf-8")
            assert text.startswith("---\n")
            assert f"truth_file: {k.value}" in text

    def test_render_filters_by_characters(self, seeded_store: TruthFileStore):
        # Add a second character's emotion arc
        seeded_store.apply_deltas(TruthDeltas(
            chapter_num=6,
            emotion_arc_entries=[
                EmotionArcEntry(character="李清漪", from_state="信任",
                                to_state="担忧", trigger="x"),
            ],
        ))
        # filter to 张远 only
        out = seeded_store.render_for_prompt(
            TruthFileKind.emotional_arcs, chapter_num=6,
            characters=["张远"],
        )
        assert "张远" in out
        assert "李清漪" not in out
