"""Tests for StorylandStateStore.apply_deltas (C2 scope)."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from storage.project_schema import ensure_creation_tables
from knowledge.storyland_state.schemas import (
    ChapterSummaryDelta, EmotionArcEntry, HookDelta, HookImportance,
    HookStatus, NumericalReconciliation, StatePatch,
    SubplotStatus, SubplotUpdate, StorylandStateDeltas,
)
from knowledge.storyland_state.store import StorylandStateStore, _hash_deltas


# ───────── fixtures ─────────

@pytest.fixture
def db_path(tmp_path: Path) -> str:
    db = tmp_path / "test.db"
    conn = sqlite3.connect(str(db))
    ensure_creation_tables(conn)
    # seed a project (FK target)
    conn.execute(
        "INSERT INTO projects(project_id, title) VALUES(?, ?)",
        ("proj1", "demo"),
    )
    conn.commit()
    conn.close()
    return str(db)


@pytest.fixture
def store(db_path: str) -> StorylandStateStore:
    return StorylandStateStore("proj1", db_path=db_path)


def _row_count(db_path: str, table: str, project_id: str = "proj1") -> int:
    with sqlite3.connect(db_path) as conn:
        cur = conn.execute(
            f"SELECT COUNT(*) FROM {table} WHERE project_id = ?",
            (project_id,),
        )
        return cur.fetchone()[0]


# ───────── hash determinism ─────────

def test_hash_is_deterministic():
    d1 = StorylandStateDeltas(
        chapter_num=5,
        hook_deltas=[
            HookDelta(description="x", action="new",
                      importance=HookImportance.A),
        ],
    )
    d2 = StorylandStateDeltas(
        chapter_num=5,
        hook_deltas=[
            HookDelta(description="x", action="new",
                      importance=HookImportance.A),
        ],
    )
    assert _hash_deltas(d1) == _hash_deltas(d2)


def test_hash_changes_on_content_change():
    base = StorylandStateDeltas(
        chapter_num=5,
        hook_deltas=[HookDelta(description="x", action="new")],
    )
    different = StorylandStateDeltas(
        chapter_num=5,
        hook_deltas=[HookDelta(description="y", action="new")],
    )
    assert _hash_deltas(base) != _hash_deltas(different)


# ───────── full apply round-trip ─────────

def test_apply_writes_all_seven_tables(store: StorylandStateStore, db_path: str):
    deltas = StorylandStateDeltas(
        chapter_num=12,
        current_state_patches=[
            StatePatch(subject="张远", predicate="在", object="青云山",
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
            HookDelta(hook_id="h_玉佩", description="玉佩之谜",
                      action="new", importance=HookImportance.A,
                      expected_payoff_chapter=15,
                      evidence="主角拾到黑衣人玉佩"),
        ],
        chapter_summary=ChapterSummaryDelta(
            summary="主角下山遭遇黑衣人",
            key_events=["遇袭", "拾到玉佩"],
            pov_character="张远", mood="紧张",
        ),
        subplot_updates=[
            SubplotUpdate(
                thread_id="sp_main", name="主角下山线",
                action="new", status_after=SubplotStatus.setup,
                related_hook_ids=["h_玉佩"],
            ),
        ],
        emotion_arc_entries=[
            EmotionArcEntry(character="张远", from_state="平静",
                            to_state="警觉", trigger="遭遇黑衣人"),
        ],
    )
    res = store.apply_deltas(deltas)
    assert res.success
    assert res.idempotent_hit is False
    assert res.chapter_num == 12
    assert res.applied_counts["current_state"] == 1
    assert res.applied_counts["character_ledger"] == 1
    assert res.applied_counts["pending_hooks"] == 1
    assert res.applied_counts["chapter_summaries"] == 1
    assert res.applied_counts["subplot_threads"] == 1
    assert res.applied_counts["emotion_arcs"] == 1

    assert _row_count(db_path, "truth_current_state") == 1
    assert _row_count(db_path, "character_ledger") == 1
    assert _row_count(db_path, "pending_hooks") == 1
    assert _row_count(db_path, "hook_events") == 1  # one "new" event
    assert _row_count(db_path, "chapter_summaries") == 1
    assert _row_count(db_path, "subplot_threads") == 1
    assert _row_count(db_path, "emotion_arcs") == 1
    assert _row_count(db_path, "truth_apply_log") == 1


# ───────── idempotency ─────────

def test_apply_is_idempotent_on_same_hash(store: StorylandStateStore, db_path: str):
    deltas = StorylandStateDeltas(
        chapter_num=3,
        hook_deltas=[HookDelta(description="x", action="new")],
    )
    r1 = store.apply_deltas(deltas)
    r2 = store.apply_deltas(deltas)
    assert r1.success and r2.success
    assert r1.idempotent_hit is False
    assert r2.idempotent_hit is True
    assert r1.deltas_hash == r2.deltas_hash
    # only one row should exist
    assert _row_count(db_path, "pending_hooks") == 1
    assert _row_count(db_path, "truth_apply_log") == 1


def test_different_deltas_apply_separately(store: StorylandStateStore, db_path: str):
    d1 = StorylandStateDeltas(
        chapter_num=3,
        hook_deltas=[HookDelta(description="A", action="new")],
    )
    d2 = StorylandStateDeltas(
        chapter_num=4,
        hook_deltas=[HookDelta(description="B", action="new")],
    )
    store.apply_deltas(d1)
    store.apply_deltas(d2)
    assert _row_count(db_path, "pending_hooks") == 2
    assert _row_count(db_path, "truth_apply_log") == 2


# ───────── atomic rollback ─────────

def test_apply_rolls_back_on_db_error(monkeypatch, store: StorylandStateStore, db_path: str):
    """Trigger a failure mid-apply and verify rollback."""
    # First, seed a successful baseline so we can check it survives.
    store.apply_deltas(StorylandStateDeltas(
        chapter_num=1,
        hook_deltas=[HookDelta(hook_id="seed", description="seed", action="new")],
    ))
    assert _row_count(db_path, "pending_hooks") == 1

    # Patch the hook apply step to blow up after running.
    from knowledge.storyland_state import store as _store_mod
    original = _store_mod.StorylandStateStore._apply_hook_deltas

    def boom(self, conn, deltas):
        original(self, conn, deltas)
        raise sqlite3.OperationalError("simulated mid-transaction failure")

    monkeypatch.setattr(_store_mod.StorylandStateStore, "_apply_hook_deltas", boom)

    bad = StorylandStateDeltas(
        chapter_num=2,
        hook_deltas=[HookDelta(description="should not land", action="new")],
        emotion_arc_entries=[
            EmotionArcEntry(character="X", from_state="a", to_state="b",
                            trigger="t"),
        ],
    )
    res = store.apply_deltas(bad)
    assert res.success is False
    assert res.sqlite_errors

    # rollback: only the seeded hook + its event survive;
    # the failed second apply must not have landed anything.
    assert _row_count(db_path, "pending_hooks") == 1
    assert _row_count(db_path, "hook_events") == 1
    assert _row_count(db_path, "emotion_arcs") == 0
    # truth_apply_log: only the FIRST successful apply's row remains;
    # the failed second apply must not write a log row.
    assert _row_count(db_path, "truth_apply_log") == 1


# ───────── hook lifecycle ─────────

def test_hook_new_then_progress_transitions_open_to_progressing(
    store: StorylandStateStore, db_path: str,
):
    store.apply_deltas(StorylandStateDeltas(
        chapter_num=1,
        hook_deltas=[HookDelta(hook_id="h1", description="x", action="new")],
    ))
    store.apply_deltas(StorylandStateDeltas(
        chapter_num=2,
        hook_deltas=[HookDelta(hook_id="h1", description="x", action="progress",
                               evidence="advance")],
    ))
    with sqlite3.connect(db_path) as conn:
        status = conn.execute(
            "SELECT status FROM pending_hooks WHERE hook_id=?", ("h1",),
        ).fetchone()[0]
    assert status == HookStatus.progressing.value
    assert _row_count(db_path, "hook_events") == 2  # new + progress


def test_hook_resolve_terminates(store: StorylandStateStore, db_path: str):
    store.apply_deltas(StorylandStateDeltas(
        chapter_num=1,
        hook_deltas=[HookDelta(hook_id="h1", description="x", action="new")],
    ))
    store.apply_deltas(StorylandStateDeltas(
        chapter_num=3,
        hook_deltas=[HookDelta(hook_id="h1", description="x", action="resolve")],
    ))
    with sqlite3.connect(db_path) as conn:
        status = conn.execute(
            "SELECT status FROM pending_hooks WHERE hook_id=?", ("h1",),
        ).fetchone()[0]
    assert status == HookStatus.resolved.value


def test_hook_mention_bumps_last_mention_only(store: StorylandStateStore, db_path: str):
    store.apply_deltas(StorylandStateDeltas(
        chapter_num=1,
        hook_deltas=[HookDelta(hook_id="h1", description="x", action="new")],
    ))
    store.apply_deltas(StorylandStateDeltas(
        chapter_num=2,
        hook_deltas=[HookDelta(hook_id="h1", description="x", action="mention")],
    ))
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT status, last_mention_chapter, last_advance_chapter "
            "FROM pending_hooks WHERE hook_id=?", ("h1",),
        ).fetchone()
    # status unchanged from 'open'; last_mention bumped; last_advance from 'new' still 1
    assert row[0] == HookStatus.open.value
    assert row[1] == 2
    assert row[2] == 1


def test_hook_id_generated_when_action_new_and_id_omitted(
    store: StorylandStateStore, db_path: str,
):
    store.apply_deltas(StorylandStateDeltas(
        chapter_num=1,
        hook_deltas=[HookDelta(description="anonymous", action="new")],
    ))
    with sqlite3.connect(db_path) as conn:
        ids = [
            row[0] for row in conn.execute(
                "SELECT hook_id FROM pending_hooks WHERE project_id=?",
                ("proj1",),
            )
        ]
    assert len(ids) == 1
    assert isinstance(ids[0], str) and ids[0]


def test_hook_unknown_id_on_non_new_is_skipped(store: StorylandStateStore, db_path: str):
    # progress action against a non-existent hook → skipped, not errored
    res = store.apply_deltas(StorylandStateDeltas(
        chapter_num=1,
        hook_deltas=[HookDelta(hook_id="ghost", description="x",
                               action="progress")],
    ))
    assert res.success
    assert _row_count(db_path, "pending_hooks") == 0
    assert _row_count(db_path, "hook_events") == 0


# ───────── pressure recomputation ─────────

def test_pressure_transition_after_threshold(store: StorylandStateStore, db_path: str):
    # Hook created Ch 1, importance B → threshold 5
    store.apply_deltas(StorylandStateDeltas(
        chapter_num=1,
        hook_deltas=[HookDelta(hook_id="hP", description="long-running",
                               action="new", importance=HookImportance.B)],
    ))
    # No advance until Ch 7 (gap == 6 ≥ 5)
    store.apply_deltas(StorylandStateDeltas(chapter_num=7))
    with sqlite3.connect(db_path) as conn:
        status = conn.execute(
            "SELECT status FROM pending_hooks WHERE hook_id=?", ("hP",),
        ).fetchone()[0]
    assert status == HookStatus.pressured.value


def test_importance_a_pressures_sooner(store: StorylandStateStore, db_path: str):
    store.apply_deltas(StorylandStateDeltas(
        chapter_num=1,
        hook_deltas=[HookDelta(hook_id="hA", description="urgent",
                               action="new", importance=HookImportance.A)],
    ))
    # threshold for A = 3 → gap 3 at Ch 4 triggers pressure
    store.apply_deltas(StorylandStateDeltas(chapter_num=4))
    with sqlite3.connect(db_path) as conn:
        status = conn.execute(
            "SELECT status FROM pending_hooks WHERE hook_id=?", ("hA",),
        ).fetchone()[0]
    assert status == HookStatus.pressured.value


# ───────── current_state versioning ─────────

def test_state_upsert_supersedes_old_triple(store: StorylandStateStore, db_path: str):
    store.apply_deltas(StorylandStateDeltas(
        chapter_num=5,
        current_state_patches=[
            StatePatch(subject="张远", predicate="在", object="青云山",
                       valid_from_chapter=5),
        ],
    ))
    store.apply_deltas(StorylandStateDeltas(
        chapter_num=8,
        current_state_patches=[
            StatePatch(subject="张远", predicate="在", object="幽冥谷",
                       valid_from_chapter=8),
        ],
    ))
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            """SELECT object, valid_from_chapter, valid_to_chapter
               FROM truth_current_state
               WHERE project_id=? AND subject=? AND predicate=?
               ORDER BY valid_from_chapter""",
            ("proj1", "张远", "在"),
        ).fetchall()
    assert len(rows) == 2
    # Old triple closed at chapter 7 (8 - 1)
    assert rows[0] == ("青云山", 5, 7)
    # New triple open-ended
    assert rows[1] == ("幽冥谷", 8, None)


def test_state_invalidate_closes_window_only(store: StorylandStateStore, db_path: str):
    store.apply_deltas(StorylandStateDeltas(
        chapter_num=2,
        current_state_patches=[
            StatePatch(subject="A", predicate="灵根", object="觉醒",
                       valid_from_chapter=2),
        ],
    ))
    store.apply_deltas(StorylandStateDeltas(
        chapter_num=5,
        current_state_patches=[
            StatePatch(subject="A", predicate="灵根", object="觉醒",
                       action="invalidate", valid_from_chapter=5),
        ],
    ))
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            """SELECT object, valid_to_chapter
               FROM truth_current_state
               WHERE project_id=? AND subject=? AND predicate=?""",
            ("proj1", "A", "灵根"),
        ).fetchall()
    assert rows == [("觉醒", 4)]  # closed at chapter 4 (5 - 1)


# ───────── chapter summary upsert ─────────

def test_chapter_summary_upserts_same_chapter(
    store: StorylandStateStore, db_path: str,
):
    store.apply_deltas(StorylandStateDeltas(
        chapter_num=3,
        chapter_summary=ChapterSummaryDelta(
            summary="v1", key_events=["a"],
        ),
    ))
    store.apply_deltas(StorylandStateDeltas(
        chapter_num=3,
        chapter_summary=ChapterSummaryDelta(
            summary="v2", key_events=["a", "b"],
        ),
    ))
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT summary_text FROM chapter_summaries "
            "WHERE project_id=? AND chapter_num=?",
            ("proj1", 3),
        ).fetchall()
    assert len(rows) == 1
    assert rows[0][0] == "v2"


# NOTE: character_matrix / character_relations removed in v3.0.
# Pairwise relationships now live on character_snapshots.relations_overrides.
# See docs/LOADER_SPEC.md Loaders 5 + 10.
