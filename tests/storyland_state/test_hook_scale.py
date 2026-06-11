"""伏笔规模四档 (spec 故事线·机制4) — payoff window + overdue transitions."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from storage.project_schema import ensure_creation_tables
from knowledge.storyland_state.schemas import (
    HOOK_SCALE_WINDOWS, HookDelta, HookImportance, HookScale,
    StorylandStateDeltas,
)
from knowledge.storyland_state.store import StorylandStateStore


@pytest.fixture
def store(tmp_path: Path) -> StorylandStateStore:
    db = tmp_path / "scale.db"
    conn = sqlite3.connect(str(db))
    ensure_creation_tables(conn)
    conn.execute("INSERT INTO projects(project_id, title) VALUES('demo','x')")
    conn.commit()
    conn.close()
    return StorylandStateStore("demo", db_path=str(db))


def _new_hook(store: StorylandStateStore, chapter: int, scale: HookScale,
              description: str, expected: int | None = None) -> dict:
    store.apply_deltas(StorylandStateDeltas(
        chapter_num=chapter,
        hook_deltas=[HookDelta(
            description=description, action="new",
            importance=HookImportance.B, scale=scale,
            expected_payoff_chapter=expected,
        )],
    ), allow_backfill=True)
    rows = store.query_pending_hooks()
    return next(h for h in rows if h["description"] == description)


class TestPayoffWindow:
    def test_windows_match_spec(self) -> None:
        assert HOOK_SCALE_WINDOWS["boomerang"] == 3
        assert HOOK_SCALE_WINDOWS["event_clue"] == 20
        assert HOOK_SCALE_WINDOWS["grand_plan"] == 100
        assert HOOK_SCALE_WINDOWS["world_truth"] is None

    def test_expected_payoff_derived_from_scale(self, store) -> None:
        h = _new_hook(store, 5, HookScale.boomerang, "回旋镖钩子")
        assert h["expected_payoff_chapter"] == 8
        h2 = _new_hook(store, 5, HookScale.grand_plan, "大计划钩子")
        assert h2["expected_payoff_chapter"] == 105

    def test_world_truth_never_gets_window(self, store) -> None:
        h = _new_hook(store, 1, HookScale.world_truth, "世界真相钩子")
        assert h["expected_payoff_chapter"] is None

    def test_user_override_wins(self, store) -> None:
        h = _new_hook(store, 5, HookScale.boomerang, "覆盖钩子", expected=50)
        assert h["expected_payoff_chapter"] == 50

    def test_scale_persisted(self, store) -> None:
        _new_hook(store, 1, HookScale.boomerang, "规模持久化")
        with sqlite3.connect(store.db_path) as conn:
            scale = conn.execute(
                "SELECT scale FROM pending_hooks WHERE description=?",
                ("规模持久化",),
            ).fetchone()[0]
        assert scale == "boomerang"


class TestOverdueTransition:
    def test_hook_pressured_after_window_exceeded(self, store) -> None:
        _new_hook(store, 1, HookScale.boomerang, "短线钩子")  # payoff = 4
        # Chapter 4 (= payoff) must NOT flip — 超期是"超过"而非"达到"。
        store.apply_deltas(StorylandStateDeltas(chapter_num=4),
                           allow_backfill=True)
        h = next(h for h in store.query_pending_hooks()
                 if h["description"] == "短线钩子")
        assert h["status"] == "open"
        # Chapter 5 (> payoff) flips to pressured.
        store.apply_deltas(StorylandStateDeltas(chapter_num=5),
                           allow_backfill=True)
        h = next(h for h in store.query_pending_hooks()
                 if h["description"] == "短线钩子")
        assert h["status"] == "pressured"

    def test_world_truth_never_pressured_by_window(self, store) -> None:
        _new_hook(store, 1, HookScale.world_truth, "终极真相")
        # Mention it every chapter so the stale-gap rule doesn't fire either.
        h = next(h for h in store.query_pending_hooks()
                 if h["description"] == "终极真相")
        for ch in (2, 3, 4):
            store.apply_deltas(StorylandStateDeltas(
                chapter_num=ch,
                hook_deltas=[HookDelta(hook_id=h["hook_id"],
                                       description="终极真相",
                                       action="mention")],
            ), allow_backfill=True)
        h = next(x for x in store.query_pending_hooks()
                 if x["description"] == "终极真相")
        assert h["status"] == "open"


class TestLoaderAnnotation:
    def test_legacy_list_includes_scale(self, store, monkeypatch) -> None:
        _new_hook(store, 1, HookScale.event_clue, "loader可见钩子")
        from ui.backend.app.services import project_store
        items = project_store.list_foreshadowing_legacy(store.db_path, "demo")
        assert items and items[0]["scale"] == "event_clue"

    def test_loader_marks_due_hooks(self, store, monkeypatch) -> None:
        """机制7: 已达/超预期回收章节 → 标注"应回收"."""
        _new_hook(store, 1, HookScale.boomerang, "窗口到期钩子")  # payoff = 4
        from ui.backend.app.services.prompt_context.loaders import foreshadowing

        monkeypatch.setattr(
            "ui.backend.app.services.project_paths.get_db_path",
            lambda: store.db_path,
        )
        before = foreshadowing.load("demo", chapter_num=3)
        due = foreshadowing.load("demo", chapter_num=4)
        assert "应回收" not in before
        assert "应回收" in due
        assert "回旋镖" in due
