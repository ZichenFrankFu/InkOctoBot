"""Regression test for the 角色卡 snapshot persistence bug.

The 角色管理 → 角色卡 page edits ``dynamic_snapshots`` directly on the
character record (the legacy path — the canonical snapshot pipeline
lives in ``snapshot_store``). Earlier code path stripped that field on
write so saved snapshots evaporated on the next load. This test pins
the round-trip to make sure the legacy editor stays usable.
"""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from storage.project_schema import ensure_creation_tables
from ui.backend.app.services import project_store


def _fresh_db() -> str:
    db = os.path.join(tempfile.mkdtemp(), "char_snap.db")
    con = sqlite3.connect(db)
    con.execute("PRAGMA foreign_keys=ON")
    ensure_creation_tables(con)
    con.execute(
        "INSERT INTO projects (project_id, title) VALUES ('p1', 't')"
    )
    con.commit()
    con.close()
    return db


def test_dynamic_snapshots_round_trip() -> None:
    db = _fresh_db()
    saved = project_store.upsert_character(db, {
        "project_id": "p1",
        "name": "阿月",
        "role": "主角",
        "dynamic_snapshots": [
            {
                "chapter": "第1章",
                "stage": "动摇",
                "personality": "外冷内热",
                "background": "刚入师门",
                "notes": "起点章",
                "relationships": [
                    {
                        "target_id": "t1", "target_name": "青鸾",
                        "affinity": 60, "priority": 1,
                        "label": "挚友", "notes": "竹马",
                    },
                ],
                "hidden_identities": [
                    {"name": "云客", "revealed_to": ["青鸾"], "notes": "伪装"},
                ],
                "layer_b": {
                    "loss_aversion": 3.2,
                    "risk_aversion_gain": 0.4,
                    "impulse_probability": 0.5,
                    "social_frequency": 4,
                    "time_discount": 0.9,
                    "value_weights": {},
                },
            },
            {
                "chapter": "第5章",
                "stage": "倾向",
                "personality": "外热内冷",
                "relationships": [],
                "hidden_identities": [],
            },
        ],
    })
    # Returned payload should already round-trip the snapshots.
    assert len(saved.get("dynamic_snapshots", [])) == 2
    # Now reload from a fresh row read.
    reload = project_store.get_character(db, saved["id"])
    assert reload is not None
    snaps = reload.get("dynamic_snapshots") or []
    assert len(snaps) == 2

    s0 = snaps[0]
    assert s0["chapter"] == "第1章"
    assert s0["stage"] == "动摇"
    assert s0["personality"] == "外冷内热"
    assert s0["relationships"][0]["target_name"] == "青鸾"
    assert s0["relationships"][0]["affinity"] == 60
    assert s0["hidden_identities"][0]["name"] == "云客"
    assert s0["hidden_identities"][0]["revealed_to"] == ["青鸾"]
    assert s0["layer_b"]["loss_aversion"] == 3.2

    s1 = snaps[1]
    assert s1["chapter"] == "第5章"
    assert s1["stage"] == "倾向"


def test_upsert_preserves_snapshots_on_unrelated_field_change() -> None:
    """Re-saving a character with a tweaked top-level field must not
    drop the snapshots that were attached on the previous write."""
    db = _fresh_db()
    saved = project_store.upsert_character(db, {
        "project_id": "p1",
        "name": "阿月",
        "personality": "v1",
        "dynamic_snapshots": [
            {"chapter": "第1章", "stage": "试探"},
        ],
    })
    # Pretend the frontend round-tripped the character and changed
    # only `personality`. The snapshots are included as the frontend
    # would always send them in `editing`.
    saved["personality"] = "v2"
    again = project_store.upsert_character(db, saved)
    assert again["personality"] == "v2"
    snaps = again.get("dynamic_snapshots") or []
    assert len(snaps) == 1
    assert snaps[0]["chapter"] == "第1章"
    assert snaps[0]["stage"] == "试探"
