"""
Tests for the event system.

Tests: EventBus, TriggerRegistry, Event types.
"""
from __future__ import annotations

import asyncio
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.event_types import Event, EventType, AgentSuggestion
from core.event_bus import EventBus
from core.triggers import TriggerRule, TriggerRegistry


class TestEventTypes(unittest.TestCase):

    def test_event_creation(self):
        evt = Event(
            event_type=EventType.CHAPTER_COMPLETED,
            source="test",
            project_id="p1",
            data={"chapter_num": 5},
        )
        self.assertEqual(evt.event_type, "CHAPTER_COMPLETED")
        d = evt.to_dict()
        self.assertIn("timestamp", d)
        self.assertEqual(d["data"]["chapter_num"], 5)

    def test_suggestion_creation(self):
        sug = AgentSuggestion(
            agent_name="evaluator",
            title="伏笔超期提醒",
            content="第3章埋的伏笔已超过5章未回收",
            severity="warning",
        )
        d = sug.to_dict()
        self.assertEqual(d["agent_name"], "evaluator")
        self.assertEqual(d["severity"], "warning")


class TestEventBus(unittest.TestCase):

    def test_subscribe_and_publish(self):
        bus = EventBus()
        received = []
        bus.subscribe("TEST_EVENT", lambda e: received.append(e))
        bus.publish(Event(event_type="TEST_EVENT", data={"x": 1}))
        self.assertEqual(len(received), 1)
        self.assertEqual(received[0].data["x"], 1)

    def test_subscribe_all(self):
        bus = EventBus()
        received = []
        bus.subscribe_all(lambda e: received.append(e))
        bus.publish(Event(event_type="A"))
        bus.publish(Event(event_type="B"))
        self.assertEqual(len(received), 2)

    def test_history(self):
        bus = EventBus()
        bus.publish(Event(event_type="A"))
        bus.publish(Event(event_type="B"))
        bus.publish(Event(event_type="A"))
        history = bus.get_history()
        self.assertEqual(len(history), 3)
        filtered = bus.get_history(event_type="A")
        self.assertEqual(len(filtered), 2)

    def test_suggestion_queue(self):
        bus = EventBus()
        bus.push_suggestion(AgentSuggestion(
            agent_name="test", title="Test", content="Content",
        ))
        suggestions = bus.get_suggestions_nowait()
        self.assertEqual(len(suggestions), 1)
        self.assertEqual(suggestions[0].agent_name, "test")

    def test_clear(self):
        bus = EventBus()
        bus.subscribe("X", lambda e: None)
        bus.publish(Event(event_type="X"))
        bus.clear()
        self.assertEqual(len(bus.get_history()), 0)


class TestTriggerRegistry(unittest.TestCase):

    def test_trigger_fires(self):
        bus = EventBus()
        registry = TriggerRegistry(bus)
        fired = []

        async def action(event):
            fired.append(event)
            return AgentSuggestion(
                agent_name="test", title="Fired", content="OK",
            )

        trigger = TriggerRule(
            agent_name="test",
            event_types=["CHAPTER_COMPLETED"],
            condition=lambda e: True,
            action=action,
            cooldown_chapters=0,
        )
        registry.register(trigger)
        bus.publish(Event(event_type="CHAPTER_COMPLETED"))
        # Give async task a moment
        loop = asyncio.new_event_loop()
        loop.run_until_complete(asyncio.sleep(0.1))
        loop.close()

    def test_cooldown(self):
        bus = EventBus()
        registry = TriggerRegistry(bus)

        trigger = TriggerRule(
            agent_name="test",
            event_types=["TEST"],
            condition=lambda e: True,
            action=lambda e: None,
            cooldown_chapters=3,
        )
        registry.register(trigger)
        self.assertTrue(trigger.can_fire(3))  # 3 - 0 = 3 >= 3
        trigger.mark_fired(3)
        self.assertFalse(trigger.can_fire(4))  # 4 - 3 = 1 < 3
        self.assertFalse(trigger.can_fire(5))  # 5 - 3 = 2 < 3
        self.assertTrue(trigger.can_fire(6))   # 6 - 3 = 3 >= 3

    def test_list_triggers(self):
        bus = EventBus()
        registry = TriggerRegistry(bus)
        registry.register(TriggerRule(
            agent_name="a", event_types=["X"],
            condition=lambda e: True, action=lambda e: None,
        ))
        triggers = registry.list_triggers()
        self.assertEqual(len(triggers), 1)
        self.assertEqual(triggers[0]["agent"], "a")


if __name__ == "__main__":
    unittest.main()
