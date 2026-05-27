"""Tests for the user_notifications service (Phase 1)."""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from storage.project_schema import ensure_creation_tables
from framework import global_event_bus
from ui.backend.app.services.notifications import (
    acknowledge, create_notification, list_notifications, mark_read,
)


def _fresh_db() -> str:
    db = os.path.join(tempfile.mkdtemp(), "n.db")
    con = sqlite3.connect(db)
    ensure_creation_tables(con)
    con.execute("INSERT INTO projects (project_id, title) VALUES ('p1','t')")
    con.commit()
    con.close()
    return db


class TestCreateAndList(unittest.TestCase):

    def setUp(self) -> None:
        global_event_bus.reset_for_tests()
        self.db = _fresh_db()

    def tearDown(self) -> None:
        global_event_bus.reset_for_tests()

    def test_create_then_list(self) -> None:
        n = create_notification(
            self.db, project_id="p1", notification_type="task_failure",
            severity="high", title="测试通知", description="some details",
            action_label="处理", action_url="/somewhere",
        )
        self.assertTrue(n.notification_id.startswith("nt_"))
        self.assertTrue(n.created_at)
        items = list_notifications(self.db, project_id="p1")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].title, "测试通知")
        self.assertEqual(items[0].severity, "high")
        self.assertEqual(items[0].action_url, "/somewhere")

    def test_severity_validation(self) -> None:
        with self.assertRaisesRegex(ValueError, "severity"):
            create_notification(
                self.db, project_id="p1", notification_type="x",
                severity="critical", title="bad",
            )

    def test_unread_only_filter(self) -> None:
        n1 = create_notification(self.db, project_id="p1",
                                  notification_type="a", severity="low", title="A")
        n2 = create_notification(self.db, project_id="p1",
                                  notification_type="b", severity="low", title="B")
        mark_read(self.db, n1.notification_id)
        unread = list_notifications(self.db, project_id="p1", unread_only=True)
        self.assertEqual(len(unread), 1)
        self.assertEqual(unread[0].notification_id, n2.notification_id)

    def test_severity_filter(self) -> None:
        create_notification(self.db, project_id="p1",
                              notification_type="x", severity="low", title="L")
        create_notification(self.db, project_id="p1",
                              notification_type="x", severity="high", title="H")
        only_high = list_notifications(self.db, project_id="p1", severity="high")
        self.assertEqual(len(only_high), 1)
        self.assertEqual(only_high[0].title, "H")


class TestMarkAndAck(unittest.TestCase):

    def setUp(self) -> None:
        global_event_bus.reset_for_tests()
        self.db = _fresh_db()

    def tearDown(self) -> None:
        global_event_bus.reset_for_tests()

    def test_mark_read_idempotent(self) -> None:
        n = create_notification(self.db, project_id="p1",
                                 notification_type="x", severity="low", title="t")
        self.assertTrue(mark_read(self.db, n.notification_id))
        # Second call returns False — already read.
        self.assertFalse(mark_read(self.db, n.notification_id))

    def test_acknowledge_sets_read_at_too(self) -> None:
        n = create_notification(self.db, project_id="p1",
                                 notification_type="x", severity="low", title="t")
        self.assertTrue(acknowledge(self.db, n.notification_id))
        items = list_notifications(self.db, project_id="p1")
        self.assertIsNotNone(items[0].read_at)
        self.assertIsNotNone(items[0].acknowledged_at)


class TestEventBusDoubleWrite(unittest.TestCase):
    """Spec contract: every create_notification also pushes an
    AgentSuggestion onto the global EventBus so the WebSocket consumer
    lights up the bell icon without polling."""

    def setUp(self) -> None:
        global_event_bus.reset_for_tests()
        self.db = _fresh_db()

    def tearDown(self) -> None:
        global_event_bus.reset_for_tests()

    def test_event_bus_receives_suggestion(self) -> None:
        bus = global_event_bus.get_event_bus()
        create_notification(
            self.db, project_id="p1",
            notification_type="chapter_summarizer_failed",
            severity="high", title="摘要失败",
            description="LLM 调用超时", action_url="/chapters/ch1/edit-summary",
        )
        suggestions = bus.get_suggestions_nowait()
        self.assertEqual(len(suggestions), 1)
        s = suggestions[0]
        # Severity high → warning in EventBus terms.
        self.assertEqual(s.severity, "warning")
        self.assertEqual(s.event_type, "notification.chapter_summarizer_failed")
        self.assertEqual(s.title, "摘要失败")
        self.assertEqual(s.data["action_url"], "/chapters/ch1/edit-summary")

    def test_push_can_be_disabled(self) -> None:
        bus = global_event_bus.get_event_bus()
        create_notification(
            self.db, project_id="p1", notification_type="x",
            severity="low", title="silent", push_to_event_bus=False,
        )
        self.assertEqual(bus.get_suggestions_nowait(), [])


if __name__ == "__main__":
    unittest.main()
