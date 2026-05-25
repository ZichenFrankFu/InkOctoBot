"""Tests for rag.truth.validators (C3): 12 cross-file rules + state audit."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from database.creation_schema import ensure_creation_tables
from rag.truth.schemas import (
    ChapterSummaryDelta, EmotionArcEntry, HookDelta, HookImportance,
    NumericalReconciliation, RelationUpdate, StatePatch, SubplotStatus,
    SubplotUpdate, TruthDeltas,
)
from rag.truth.store import TruthFileStore
from rag.truth.validators import validate_deltas, validate_state


# ─────── fixtures ───────

@pytest.fixture
def db_path(tmp_path: Path) -> str:
    db = tmp_path / "test.db"
    conn = sqlite3.connect(str(db))
    ensure_creation_tables(conn)
    conn.execute("INSERT INTO projects(project_id, title) VALUES('p1','demo')")
    conn.commit()
    conn.close()
    return str(db)


@pytest.fixture
def store(db_path: str) -> TruthFileStore:
    return TruthFileStore("p1", db_path=db_path)


KNOWN_CHARS = {"张远", "李清漪", "黑衣人"}


def _issue_ids(issues) -> list[str]:
    return [i.rule_id for i in issues]


# ────────── Layer 1: within-delta rules ──────────

class TestWithinDeltaRules:

    def test_hook_no_duplicate_id_pos(self, store: TruthFileStore):
        deltas = TruthDeltas(
            chapter_num=1,
            hook_deltas=[
                HookDelta(hook_id="h1", description="a", action="new"),
                HookDelta(hook_id="h1", description="b", action="new"),
            ],
        )
        issues = validate_deltas(store, deltas, known_characters=KNOWN_CHARS)
        assert "hook.no_duplicate_id" in _issue_ids(issues)

    def test_hook_no_duplicate_id_neg(self, store: TruthFileStore):
        deltas = TruthDeltas(
            chapter_num=1,
            hook_deltas=[
                HookDelta(hook_id="h1", description="a", action="new"),
                HookDelta(hook_id="h2", description="b", action="new"),
            ],
        )
        issues = validate_deltas(store, deltas, known_characters=KNOWN_CHARS)
        assert "hook.no_duplicate_id" not in _issue_ids(issues)

    def test_ledger_closed_equation_violation(self, store: TruthFileStore):
        deltas = TruthDeltas(
            chapter_num=1,
            particle_reconciliations=[
                NumericalReconciliation(
                    character="张远", resource="灵气",
                    old_value=80, operation="subtract", delta=-30,
                    new_value=60,  # 80 + (-30) = 50, not 60 → violation
                    reason="施展剑诀", in_text_evidence="灵气损耗",
                ),
            ],
        )
        issues = validate_deltas(store, deltas, known_characters=KNOWN_CHARS)
        assert "ledger.closed_equation" in _issue_ids(issues)

    def test_ledger_closed_equation_ok(self, store: TruthFileStore):
        deltas = TruthDeltas(
            chapter_num=1,
            particle_reconciliations=[
                NumericalReconciliation(
                    character="张远", resource="灵气",
                    old_value=80, operation="subtract", delta=-30, new_value=50,
                    reason="ok", in_text_evidence="ok",
                ),
            ],
        )
        issues = validate_deltas(store, deltas, known_characters=KNOWN_CHARS)
        assert "ledger.closed_equation" not in _issue_ids(issues)

    def test_ledger_set_operation_skips_equation(self, store: TruthFileStore):
        """operation=set: new_value is the target; no equation closure."""
        deltas = TruthDeltas(
            chapter_num=1,
            particle_reconciliations=[
                NumericalReconciliation(
                    character="张远", resource="灵气",
                    old_value=0, operation="set", delta=0, new_value=100,
                    reason="初始化", in_text_evidence="ok",
                ),
            ],
        )
        issues = validate_deltas(store, deltas, known_characters=KNOWN_CHARS)
        assert "ledger.closed_equation" not in _issue_ids(issues)

    def test_state_conflicting_triple_warns(self, store: TruthFileStore):
        deltas = TruthDeltas(
            chapter_num=3,
            current_state_patches=[
                StatePatch(subject="A", predicate="情绪", object="平静",
                           action="upsert", valid_from_chapter=3),
                StatePatch(subject="A", predicate="情绪", object="平静",
                           action="invalidate", valid_from_chapter=3),
            ],
        )
        issues = validate_deltas(store, deltas, known_characters=KNOWN_CHARS)
        issue = [i for i in issues if i.rule_id == "state.no_conflicting_triple"]
        assert len(issue) == 1
        assert issue[0].severity == "warning"


# ────────── Layer 2: known_characters cross-ref ──────────

class TestCharacterXref:

    def test_emotion_unknown_character_errors(self, store: TruthFileStore):
        deltas = TruthDeltas(
            chapter_num=1,
            emotion_arc_entries=[
                EmotionArcEntry(character="某无名角色", from_state="a",
                                to_state="b", trigger="x"),
            ],
        )
        issues = validate_deltas(store, deltas, known_characters=KNOWN_CHARS)
        assert "xref.character_in_emotion" in _issue_ids(issues)

    def test_emotion_known_character_ok(self, store: TruthFileStore):
        deltas = TruthDeltas(
            chapter_num=1,
            emotion_arc_entries=[
                EmotionArcEntry(character="张远", from_state="a",
                                to_state="b", trigger="x"),
            ],
        )
        issues = validate_deltas(store, deltas, known_characters=KNOWN_CHARS)
        assert "xref.character_in_emotion" not in _issue_ids(issues)

    def test_relation_either_side_unknown(self, store: TruthFileStore):
        deltas = TruthDeltas(
            chapter_num=1,
            character_relation_updates=[
                RelationUpdate(character_a="张远", character_b="无人",
                               relation_type="陌生", sentiment_score=0,
                               trust_level=10),
            ],
        )
        issues = validate_deltas(store, deltas, known_characters=KNOWN_CHARS)
        assert "xref.character_in_relation" in _issue_ids(issues)

    def test_xref_skipped_when_known_characters_none(self, store: TruthFileStore):
        """When the resolver can't find characters, rule degrades to no-op."""
        deltas = TruthDeltas(
            chapter_num=1,
            emotion_arc_entries=[
                EmotionArcEntry(character="X", from_state="a", to_state="b",
                                trigger="t"),
            ],
        )
        issues = validate_deltas(store, deltas, known_characters=set())
        # empty set → rule runs but X not in {} → error.
        # Passing None → no-op:
        issues2 = validate_deltas(store, deltas, known_characters=None)
        # In test env data_api can't be reached → resolver returns None → skip.
        assert "xref.character_in_emotion" in _issue_ids(issues)
        assert "xref.character_in_emotion" not in _issue_ids(issues2)


# ────────── Layer 3: DB-aware rules ──────────

class TestDbRules:

    def test_chapter_monotonic_violation(self, store: TruthFileStore, db_path: str):
        store.apply_deltas(TruthDeltas(chapter_num=5))
        issues = validate_deltas(
            store, TruthDeltas(chapter_num=3),
            known_characters=KNOWN_CHARS,
        )
        rule_ids = _issue_ids(issues)
        assert "chapter.monotonic" in rule_ids

    def test_chapter_monotonic_ok(self, store: TruthFileStore):
        store.apply_deltas(TruthDeltas(chapter_num=5))
        issues = validate_deltas(
            store, TruthDeltas(chapter_num=6),
            known_characters=KNOWN_CHARS,
        )
        assert "chapter.monotonic" not in _issue_ids(issues)

    def test_chapter_monotonic_allow_backfill_skips(self, store: TruthFileStore):
        store.apply_deltas(TruthDeltas(chapter_num=5))
        issues = validate_deltas(
            store, TruthDeltas(chapter_num=3),
            known_characters=KNOWN_CHARS, allow_backfill=True,
        )
        assert "chapter.monotonic" not in _issue_ids(issues)

    def test_hook_orphan_progress(self, store: TruthFileStore):
        deltas = TruthDeltas(
            chapter_num=1,
            hook_deltas=[
                HookDelta(hook_id="missing", description="x",
                          action="progress"),
            ],
        )
        issues = validate_deltas(store, deltas, known_characters=KNOWN_CHARS)
        assert "hook.no_orphan_progress" in _issue_ids(issues)

    def test_hook_orphan_progress_satisfied_by_new_in_batch(
        self, store: TruthFileStore,
    ):
        deltas = TruthDeltas(
            chapter_num=1,
            hook_deltas=[
                HookDelta(hook_id="h1", description="x", action="new"),
                HookDelta(hook_id="h1", description="x", action="progress"),
            ],
        )
        # h1 is duplicate, but "new" then "progress" in same batch should
        # not be an orphan reference even if duplicate
        issues = validate_deltas(store, deltas, known_characters=KNOWN_CHARS)
        # h1 dup will fire; orphan should NOT
        assert "hook.no_orphan_progress" not in _issue_ids(issues)

    def test_hook_transition_against_terminal_errors(self, store: TruthFileStore):
        store.apply_deltas(TruthDeltas(
            chapter_num=1,
            hook_deltas=[HookDelta(hook_id="hT", description="x", action="new")],
        ))
        store.apply_deltas(TruthDeltas(
            chapter_num=2,
            hook_deltas=[HookDelta(hook_id="hT", description="x",
                                   action="resolve")],
        ))
        # Now try to progress the resolved hook → should error
        deltas = TruthDeltas(
            chapter_num=3,
            hook_deltas=[HookDelta(hook_id="hT", description="x",
                                   action="progress")],
        )
        issues = validate_deltas(store, deltas, known_characters=KNOWN_CHARS)
        assert "hook.transition_valid" in _issue_ids(issues)

    def test_ledger_matches_current_violation(self, store: TruthFileStore):
        # Seed ledger: 张远.灵气 = 80 at chapter 1
        store.apply_deltas(TruthDeltas(
            chapter_num=1,
            particle_reconciliations=[
                NumericalReconciliation(
                    character="张远", resource="灵气",
                    old_value=0, operation="set", delta=0, new_value=80,
                    reason="初始化", in_text_evidence="灵气80",
                ),
            ],
        ))
        # Now claim old_value=90 at chapter 2 → mismatch with ledger=80
        deltas = TruthDeltas(
            chapter_num=2,
            particle_reconciliations=[
                NumericalReconciliation(
                    character="张远", resource="灵气",
                    old_value=90, operation="subtract", delta=-30, new_value=60,
                    reason="x", in_text_evidence="y",
                ),
            ],
        )
        issues = validate_deltas(store, deltas, known_characters=KNOWN_CHARS)
        assert "ledger.matches_current" in _issue_ids(issues)

    def test_ledger_matches_current_no_prior_record(self, store: TruthFileStore):
        # First reconciliation for this resource: no prior record → accept any
        deltas = TruthDeltas(
            chapter_num=1,
            particle_reconciliations=[
                NumericalReconciliation(
                    character="张远", resource="灵气",
                    old_value=80, operation="subtract", delta=-30, new_value=50,
                    reason="x", in_text_evidence="y",
                ),
            ],
        )
        issues = validate_deltas(store, deltas, known_characters=KNOWN_CHARS)
        assert "ledger.matches_current" not in _issue_ids(issues)

    def test_xref_hook_in_subplot_violation(self, store: TruthFileStore):
        deltas = TruthDeltas(
            chapter_num=1,
            subplot_updates=[
                SubplotUpdate(name="sp1", action="new",
                              status_after=SubplotStatus.setup,
                              related_hook_ids=["ghost_hook"]),
            ],
        )
        issues = validate_deltas(store, deltas, known_characters=KNOWN_CHARS)
        assert "xref.hook_in_subplot" in _issue_ids(issues)

    def test_xref_hook_in_subplot_satisfied_by_new(self, store: TruthFileStore):
        deltas = TruthDeltas(
            chapter_num=1,
            hook_deltas=[HookDelta(hook_id="hN", description="x", action="new")],
            subplot_updates=[
                SubplotUpdate(name="sp1", action="new",
                              status_after=SubplotStatus.setup,
                              related_hook_ids=["hN"]),
            ],
        )
        issues = validate_deltas(store, deltas, known_characters=KNOWN_CHARS)
        assert "xref.hook_in_subplot" not in _issue_ids(issues)

    def test_relation_symmetric_drift_warns(self, store: TruthFileStore):
        # Pre-existing reverse relation
        store.apply_deltas(TruthDeltas(
            chapter_num=1,
            character_relation_updates=[
                RelationUpdate(character_a="李清漪", character_b="张远",
                               relation_type="盟友", sentiment_score=80,
                               trust_level=80),
            ],
        ))
        # New (A,B) with sentiment far diverged from (B,A)=80
        deltas = TruthDeltas(
            chapter_num=2,
            character_relation_updates=[
                RelationUpdate(character_a="张远", character_b="李清漪",
                               relation_type="敌对", sentiment_score=-50,
                               trust_level=10),
            ],
        )
        issues = validate_deltas(store, deltas, known_characters=KNOWN_CHARS)
        ids = _issue_ids(issues)
        assert "relation.symmetric_sentiment_drift" in ids
        # warning-level only
        sd = next(i for i in issues
                  if i.rule_id == "relation.symmetric_sentiment_drift")
        assert sd.severity == "warning"

    def test_subplot_hook_resolved_should_advance(self, store: TruthFileStore):
        # Seed: subplot in setup referencing hook hX
        store.apply_deltas(TruthDeltas(
            chapter_num=1,
            hook_deltas=[HookDelta(hook_id="hX", description="x", action="new")],
            subplot_updates=[
                SubplotUpdate(thread_id="sp", name="sp1", action="new",
                              status_after=SubplotStatus.setup,
                              related_hook_ids=["hX"]),
            ],
        ))
        deltas = TruthDeltas(
            chapter_num=2,
            hook_deltas=[HookDelta(hook_id="hX", description="x",
                                   action="resolve")],
        )
        issues = validate_deltas(store, deltas, known_characters=KNOWN_CHARS)
        ids = _issue_ids(issues)
        assert "subplot.hook_resolved_subplot_should_advance" in ids


# ────────── Integration: apply_deltas with validate=True ──────────

class TestApplyWithValidate:

    def test_validate_blocks_apply_on_error(
        self, store: TruthFileStore, db_path: str,
    ):
        bad = TruthDeltas(
            chapter_num=1,
            particle_reconciliations=[
                NumericalReconciliation(
                    character="张远", resource="灵气",
                    old_value=80, operation="subtract", delta=-30, new_value=60,  # wrong
                    reason="x", in_text_evidence="y",
                ),
            ],
        )
        res = store.apply_deltas(bad, validate=True,
                                 known_characters=KNOWN_CHARS)
        assert res.success is False
        assert any(i.rule_id == "ledger.closed_equation"
                   for i in res.cross_ref_issues)
        # nothing landed
        with sqlite3.connect(db_path) as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM character_ledger WHERE project_id=?",
                ("p1",),
            ).fetchone()[0]
        assert count == 0

    def test_validate_warnings_dont_block(self, store: TruthFileStore):
        # Drift warning only; should still land
        store.apply_deltas(TruthDeltas(
            chapter_num=1,
            character_relation_updates=[
                RelationUpdate(character_a="李清漪", character_b="张远",
                               relation_type="盟友", sentiment_score=80,
                               trust_level=80),
            ],
        ))
        res = store.apply_deltas(
            TruthDeltas(
                chapter_num=2,
                character_relation_updates=[
                    RelationUpdate(character_a="张远", character_b="李清漪",
                                   relation_type="敌对", sentiment_score=-50,
                                   trust_level=10),
                ],
            ),
            validate=True, known_characters=KNOWN_CHARS,
        )
        assert res.success is True
        assert any(i.rule_id == "relation.symmetric_sentiment_drift"
                   for i in res.cross_ref_issues)


# ────────── validate_state (no deltas) ──────────

class TestValidateState:

    def test_validate_state_clean(self, store: TruthFileStore):
        store.apply_deltas(TruthDeltas(
            chapter_num=1,
            hook_deltas=[HookDelta(hook_id="h", description="x", action="new")],
        ))
        report = validate_state(store, known_characters=KNOWN_CHARS)
        assert report.passed is True
        assert report.stats["pending_hooks"] == 1

    def test_validate_state_orphan_subplot_warning(
        self, store: TruthFileStore, db_path: str,
    ):
        # Seed: subplot referencing a hook, then delete the hook directly
        store.apply_deltas(TruthDeltas(
            chapter_num=1,
            hook_deltas=[HookDelta(hook_id="hX", description="x", action="new")],
            subplot_updates=[
                SubplotUpdate(thread_id="sp", name="sp1", action="new",
                              status_after=SubplotStatus.setup,
                              related_hook_ids=["hX"]),
            ],
        ))
        with sqlite3.connect(db_path) as conn:
            conn.execute("DELETE FROM pending_hooks WHERE hook_id='hX'")
            conn.commit()
        report = validate_state(store, known_characters=KNOWN_CHARS)
        assert any(i.rule_id == "audit.orphaned_subplot_hooks"
                   for i in report.issues)
