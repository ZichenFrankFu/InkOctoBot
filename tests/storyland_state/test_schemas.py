"""Unit tests for knowledge.storyland_state.schemas — Pydantic model behavior."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from knowledge.storyland_state.schemas import (
    SPOTriple, StatePatch, NumericalReconciliation,
    HookDelta, HookStatus, HookImportance,
    ChapterSummaryDelta, SubplotUpdate, SubplotStatus,
    EmotionArcEntry,
    StorylandStateDeltas, ApplyResult,
    ValidationIssue, ValidationReport,
    StorylandStateKind,
)


# ──────────────── frozen-ness ────────────────

def test_spo_triple_is_frozen():
    t = SPOTriple(subject="张远", predicate="在", object="青云山", valid_from_chapter=5)
    with pytest.raises(ValidationError):
        t.subject = "李清漪"  # type: ignore[misc]


def test_hook_delta_is_frozen():
    h = HookDelta(description="玉佩", action="new", importance=HookImportance.A)
    with pytest.raises(ValidationError):
        h.action = "resolve"  # type: ignore[misc]


def test_truth_deltas_is_frozen():
    d = StorylandStateDeltas(chapter_num=3)
    with pytest.raises(ValidationError):
        d.chapter_num = 4  # type: ignore[misc]


# ──────────────── enum coverage ────────────────

def test_storyland_state_kind_has_six_members():
    """character_matrix removed in v3.0 — relations moved to character_snapshots."""
    assert len(list(StorylandStateKind)) == 6
    expected = {
        "current_state", "particle_ledger", "pending_hooks",
        "chapter_summaries", "subplot_board", "emotional_arcs",
    }
    assert {k.value for k in StorylandStateKind} == expected


def test_hook_status_lifecycle_values():
    expected = {"open", "progressing", "pressured", "near_payoff",
                "resolved", "abandoned"}
    assert {s.value for s in HookStatus} == expected


def test_subplot_status_values():
    expected = {"setup", "building", "climax", "resolution", "dormant"}
    assert {s.value for s in SubplotStatus} == expected


# ──────────────── field validation ────────────────

def test_reconciliation_operations_literal():
    NumericalReconciliation(
        character="张远", resource="灵气",
        old_value=80, operation="subtract", delta=-30, new_value=50,
        reason="施展破云剑诀", in_text_evidence="张远体内灵气只剩五成",
    )
    with pytest.raises(ValidationError):
        NumericalReconciliation(
            character="张远", resource="灵气",
            old_value=80, operation="divide", delta=-30, new_value=50,  # type: ignore[arg-type]
            reason="x", in_text_evidence="y",
        )


def test_state_patch_action_default_upsert():
    p = StatePatch(subject="A", predicate="在", object="B", valid_from_chapter=1)
    assert p.action == "upsert"


def test_hook_delta_default_importance_is_b():
    h = HookDelta(description="x", action="new")
    assert h.importance == HookImportance.B


# ──────────────── bundle composition ────────────────

def test_truth_deltas_defaults_empty():
    d = StorylandStateDeltas(chapter_num=5)
    assert d.chapter_num == 5
    assert d.current_state_patches == []
    assert d.particle_reconciliations == []
    assert d.hook_deltas == []
    assert d.chapter_summary is None
    assert d.subplot_updates == []
    assert d.emotion_arc_entries == []


def test_truth_deltas_full_bundle_constructs():
    d = StorylandStateDeltas(
        chapter_num=12,
        current_state_patches=[
            StatePatch(subject="张远", predicate="情绪", object="怀疑",
                       valid_from_chapter=12),
        ],
        particle_reconciliations=[
            NumericalReconciliation(
                character="张远", resource="灵气",
                old_value=80, operation="subtract", delta=-30, new_value=50,
                reason="施展破云剑诀", in_text_evidence="灵气只剩五成",
            ),
        ],
        hook_deltas=[
            HookDelta(description="玉佩之谜", action="new",
                      importance=HookImportance.A),
        ],
        chapter_summary=ChapterSummaryDelta(
            summary="张远施展剑诀消耗灵气", key_events=["对战", "灵气损耗"],
        ),
        subplot_updates=[
            SubplotUpdate(name="宗门内斗", action="new",
                          status_after=SubplotStatus.setup),
        ],
        emotion_arc_entries=[
            EmotionArcEntry(character="张远", from_state="信任",
                            to_state="怀疑", trigger="宗主隐瞒真相"),
        ],
    )
    assert d.chapter_num == 12
    assert len(d.current_state_patches) == 1
    assert d.chapter_summary is not None


# ──────────────── apply result + validation report ────────────────

def test_apply_result_defaults():
    r = ApplyResult(success=True, chapter_num=5)
    assert r.success is True
    assert r.applied_counts == {}
    assert r.cross_ref_issues == []
    assert r.sqlite_errors == []
    assert r.deltas_hash == ""
    assert r.idempotent_hit is False


def test_validation_report_round_trip():
    issue = ValidationIssue(
        rule_id="ledger.closed_equation", severity="error",
        message="80-30=50 not equal to 60", location="character_ledger[0]",
    )
    rep = ValidationReport(passed=False, issues=[issue], stats={"hooks": 5})
    assert rep.passed is False
    assert rep.issues[0].rule_id == "ledger.closed_equation"
    assert rep.stats["hooks"] == 5
