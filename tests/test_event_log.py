"""EventBus 持久化日志 (spec EventBus·机制3/机制4)."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from framework import event_log
from framework.event_bus import EventBus
from framework.event_types import Event
from storage.project_schema import ensure_creation_tables


@pytest.fixture
def db(tmp_path: Path, monkeypatch) -> str:
    path = tmp_path / "oplog.db"
    con = sqlite3.connect(str(path))
    ensure_creation_tables(con)
    con.commit()
    con.close()
    monkeypatch.setattr(event_log, "_db_path", lambda: str(path))
    event_log.reset_for_tests()
    return str(path)


def test_published_events_persist_with_full_fields(db: str) -> None:
    bus = EventBus()
    event_log.attach_to_bus(bus)
    bus.publish(Event(
        event_type="GENERATION_STEP_COMPLETED", source="writer",
        project_id="p1", data={"step": "chapter_assembly", "chapter": 3},
    ))
    bus.publish(Event(event_type="USER_EDIT_SAVED", source="editor",
                      project_id="p2", data={}))
    rows = event_log.query_log(db)
    assert len(rows) == 2
    # 机制3: 时间戳 / 事件类型 / 来源模块 / 关联数据
    newest = rows[0]
    assert newest["event_type"] == "USER_EDIT_SAVED"
    assert newest["source"] == "editor"
    assert newest["event_ts"] > 0
    oldest = rows[1]
    assert oldest["data"] == {"step": "chapter_assembly", "chapter": 3}


def test_query_filters(db: str) -> None:
    bus = EventBus()
    event_log.attach_to_bus(bus)
    for i in range(3):
        bus.publish(Event(event_type="A", project_id="p1", data={"i": i}))
    bus.publish(Event(event_type="B", project_id="p2"))
    assert len(event_log.query_log(db, project_id="p1")) == 3
    assert len(event_log.query_log(db, event_type="B")) == 1
    assert len(event_log.query_log(db, project_id="p1", limit=2)) == 2


def test_attach_is_idempotent(db: str) -> None:
    bus = EventBus()
    event_log.attach_to_bus(bus)
    event_log.attach_to_bus(bus)   # second attach must not double-write
    bus.publish(Event(event_type="A", project_id="p1"))
    assert len(event_log.query_log(db)) == 1


def test_write_failure_never_breaks_publish(db: str, monkeypatch) -> None:
    bus = EventBus()
    event_log.attach_to_bus(bus)
    monkeypatch.setattr(event_log, "_db_path",
                        lambda: "/nonexistent/dir/x.db")
    # publish() must survive the listener failing.
    bus.publish(Event(event_type="A", project_id="p1"))
    assert bus.get_history()[-1]["event_type"] == "A"
