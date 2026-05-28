"""Tests for knowledge.storyland_state.migrate (C5)."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from storage.project_schema import ensure_creation_tables
from knowledge.storyland_state import migrate as M
from knowledge.storyland_state.store import StorylandStateStore


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
def store(db_path: str) -> StorylandStateStore:
    return StorylandStateStore("p1", db_path=db_path)


# ─────── foreshadowing_json ───────

class TestForeshadowingMigration:

    def test_no_file_marks_skipped(
        self, store: StorylandStateStore, monkeypatch, tmp_path: Path,
    ):
        monkeypatch.setattr(M, "_foreshadowing_json_path",
                            lambda _: tmp_path / "missing.json")
        rep = M.migrate_foreshadowing_json(store)
        assert rep.success is True   # success means "no errors"
        assert "no file" in rep.skipped_reason

    def test_empty_items_skipped(
        self, store: StorylandStateStore, monkeypatch, tmp_path: Path,
    ):
        path = tmp_path / "fs.json"
        path.write_text(json.dumps({"items": []}), "utf-8")
        monkeypatch.setattr(M, "_foreshadowing_json_path", lambda _: path)
        rep = M.migrate_foreshadowing_json(store)
        assert "empty" in rep.skipped_reason

    def test_two_items_split_by_origin(
        self, store: StorylandStateStore, db_path: str, monkeypatch, tmp_path: Path,
    ):
        path = tmp_path / "fs.json"
        path.write_text(json.dumps({
            "items": [
                {"id": "f1", "title": "玉佩之谜", "content": "黑衣人的玉佩",
                 "chapter_ids": ["ch3", "ch10"]},
                {"id": "f2", "title": "核心秘密", "content": "宗主的真实身份",
                 "chapter_ids": ["ch5"]},
            ]
        }, ensure_ascii=False), "utf-8")
        monkeypatch.setattr(M, "_foreshadowing_json_path", lambda _: path)

        rep = M.migrate_foreshadowing_json(store)
        assert rep.success
        # two distinct origins → two batches
        assert rep.batches_applied == 2
        assert rep.rows_emitted["hook_deltas"] == 2

        # f2 contains '核心' → importance A
        with sqlite3.connect(db_path) as conn:
            rows = {
                r[1]: (r[0], r[2]) for r in conn.execute(
                    "SELECT importance, description, origin_chapter "
                    "FROM pending_hooks WHERE project_id='p1'",
                )
            }
        assert "玉佩之谜" in rows
        assert rows["核心秘密"][0] == "A"

    def test_dry_run_writes_nothing(
        self, store: StorylandStateStore, db_path: str, monkeypatch, tmp_path: Path,
    ):
        path = tmp_path / "fs.json"
        path.write_text(json.dumps({
            "items": [{"id": "f1", "title": "x", "content": "y",
                       "chapter_ids": ["ch1"]}]
        }), "utf-8")
        monkeypatch.setattr(M, "_foreshadowing_json_path", lambda _: path)
        rep = M.migrate_foreshadowing_json(store, dry_run=True)
        assert rep.batches_applied == 1
        with sqlite3.connect(db_path) as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM pending_hooks WHERE project_id='p1'",
            ).fetchone()[0]
        assert count == 0  # dry run → no writes

    def test_idempotent_rerun(
        self, store: StorylandStateStore, db_path: str, monkeypatch, tmp_path: Path,
    ):
        path = tmp_path / "fs.json"
        path.write_text(json.dumps({
            "items": [{"id": "f1", "title": "x", "chapter_ids": ["ch2"]}]
        }, ensure_ascii=False), "utf-8")
        monkeypatch.setattr(M, "_foreshadowing_json_path", lambda _: path)
        M.migrate_foreshadowing_json(store)
        rep2 = M.migrate_foreshadowing_json(store)
        # second call must hit idempotency
        assert rep2.apply_results[0].idempotent_hit


# ─────── episodic_events ───────
#
# The v2 schema removed foreshadow_status / foreshadow_target_chapter
# from episodic_events. ``migrate_episodic_events`` is a one-shot
# migration that runs against pre-v2 DBs. To exercise it we have to
# rebuild a v1-shaped episodic_events table — the active schema no
# longer carries those columns.


def _rebuild_v1_episodic_events(db_path: str) -> None:
    """Replace v2 episodic_events with the v1 shape so the migrator has work to do."""
    with sqlite3.connect(db_path) as conn:
        conn.execute("DROP TABLE IF EXISTS episodic_events")
        conn.execute(
            """CREATE TABLE episodic_events (
                   event_id TEXT PRIMARY KEY,
                   project_id TEXT NOT NULL,
                   chapter_num INTEGER NOT NULL,
                   scene_index INTEGER NOT NULL DEFAULT 0,
                   event_type TEXT NOT NULL DEFAULT 'plot',
                   description TEXT NOT NULL DEFAULT '',
                   characters_json TEXT NOT NULL DEFAULT '[]',
                   causality_json TEXT NOT NULL DEFAULT '{}',
                   foreshadow_status TEXT DEFAULT NULL,
                   foreshadow_target_chapter INTEGER DEFAULT NULL,
                   importance INTEGER NOT NULL DEFAULT 3,
                   created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"""
        )
        conn.commit()


class TestEpisodicEventsMigration:

    def test_no_planted_skipped(self, store: StorylandStateStore, db_path: str):
        # v2 columns absent → migrator says "nothing to do".
        rep = M.migrate_episodic_events(store)
        assert "no planted" in rep.skipped_reason

    def test_planted_events_migrated(
        self, store: StorylandStateStore, db_path: str,
    ):
        _rebuild_v1_episodic_events(db_path)
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                """INSERT INTO episodic_events(event_id, project_id, chapter_num,
                      event_type, description, importance, foreshadow_status,
                      foreshadow_target_chapter)
                   VALUES(?, ?, ?, ?, ?, ?, ?, ?)""",
                ("ev1", "p1", 3, "foreshadowing", "黑衣人留下的玉佩",
                 4, "planted", 10),
            )
            conn.commit()
        rep = M.migrate_episodic_events(store)
        assert rep.success
        assert rep.rows_emitted["hook_deltas"] == 1
        with sqlite3.connect(db_path) as conn:
            row = conn.execute(
                "SELECT description, importance FROM pending_hooks "
                "WHERE project_id='p1'",
            ).fetchone()
        assert row[0] == "黑衣人留下的玉佩"
        assert row[1] == "A"  # importance=4 → A

    def test_dedup_existing_description(
        self, store: StorylandStateStore, db_path: str,
    ):
        _rebuild_v1_episodic_events(db_path)
        # seed both episodic and pending_hooks with same description
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                """INSERT INTO episodic_events(event_id, project_id, chapter_num,
                      event_type, description, foreshadow_status)
                   VALUES(?,?,?,?,?,?)""",
                ("ev1", "p1", 3, "foreshadowing", "玉佩", "planted"),
            )
            conn.execute(
                """INSERT INTO pending_hooks(hook_id, project_id, description,
                      origin_chapter) VALUES(?,?,?,?)""",
                ("h0", "p1", "玉佩", 1),
            )
            conn.commit()
        rep = M.migrate_episodic_events(store)
        # no batches applied since description dup
        assert "all events already present" in rep.skipped_reason


# ─────── character_cards ───────

class TestCharacterCardsMigration:

    def test_no_characters_skipped(self, store: StorylandStateStore, monkeypatch):
        monkeypatch.setattr(M, "_character_rows", lambda _: [])
        rep = M.migrate_character_cards(store)
        assert "no character" in rep.skipped_reason

    def test_state_patches_emitted_for_location_mood(
        self, store: StorylandStateStore, db_path: str, monkeypatch,
    ):
        monkeypatch.setattr(M, "_character_rows", lambda _: [
            {"name": "张远", "current_location": "青云山", "mood": "平静"},
        ])
        rep = M.migrate_character_cards(store)
        assert rep.success
        assert rep.rows_emitted["state_patches"] == 2  # location + mood
        with sqlite3.connect(db_path) as conn:
            rows = conn.execute(
                "SELECT subject, predicate, object FROM truth_current_state "
                "WHERE project_id='p1' ORDER BY predicate",
            ).fetchall()
        assert ("张远", "在", "青云山") in rows
        assert ("张远", "情绪", "平静") in rows

    # NOTE: character relations no longer migrate into the state store —
    # they now live on character_snapshots.relations_overrides (LOADER_SPEC
    # Loaders 5 + 10). Whatever relation data sits on the card JSON is
    # left there for the snapshot import step (Batch 5).


# ─────── chapter_summaries → emotion_arcs ───────

class TestChapterSummariesMigration:

    def test_too_few_summaries_skipped(self, store: StorylandStateStore):
        rep = M.migrate_chapter_summaries(store)
        assert "fewer than 2" in rep.skipped_reason

    def test_mood_transitions_extracted(
        self, store: StorylandStateStore, db_path: str,
    ):
        # seed two chapter_summaries with character_states_json having mood diff
        with sqlite3.connect(db_path) as conn:
            for ch, states in [
                (1, {"张远": {"mood": "平静"}}),
                (2, {"张远": {"mood": "警觉"}}),
            ]:
                conn.execute(
                    """INSERT INTO chapter_summaries(summary_id, project_id,
                          chapter_num, summary_text, key_events_json,
                          character_states_json)
                       VALUES(?,?,?,?,?,?)""",
                    (
                        f"s{ch}", "p1", ch, "x", "[]",
                        json.dumps(states, ensure_ascii=False),
                    ),
                )
            conn.commit()
        rep = M.migrate_chapter_summaries(store)
        assert rep.success
        assert rep.rows_emitted["emotion_arc_entries"] == 1
        with sqlite3.connect(db_path) as conn:
            row = conn.execute(
                "SELECT character_name, from_state, to_state FROM emotion_arcs "
                "WHERE project_id='p1'",
            ).fetchone()
        assert row == ("张远", "平静", "警觉")


# ─────── orchestrator ───────

def test_migrate_all_runs_all_sources(store: StorylandStateStore, monkeypatch):
    # Stub each individual function so we can verify orchestration
    calls = []

    def make_stub(name):
        def fn(store, dry_run=False):
            calls.append((name, dry_run))
            return M.MigrationReport(source=name)
        return fn

    monkeypatch.setattr(M, "migrate_foreshadowing_json",
                        make_stub("foreshadowing_json"))
    monkeypatch.setattr(M, "migrate_episodic_events",
                        make_stub("episodic_events"))
    monkeypatch.setattr(M, "migrate_character_cards",
                        make_stub("character_cards"))
    monkeypatch.setattr(M, "migrate_chapter_summaries",
                        make_stub("chapter_summaries"))

    reports = M.migrate_all(store, dry_run=True)
    assert set(reports.keys()) == {
        "foreshadowing_json", "episodic_events",
        "character_cards", "chapter_summaries",
    }
    assert all(d for (_, d) in calls)


def test_migrate_all_subset(store: StorylandStateStore, monkeypatch):
    monkeypatch.setattr(M, "migrate_foreshadowing_json",
                        lambda s, dry_run=False:
                        M.MigrationReport(source="foreshadowing_json"))
    reports = M.migrate_all(store, sources=["foreshadowing_json"])
    assert set(reports.keys()) == {"foreshadowing_json"}
