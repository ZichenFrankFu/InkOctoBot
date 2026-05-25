"""Tests for core.event_bus and core.event_types."""
import pytest
from framework.event_types import Event, EventType, AgentSuggestion
from framework.event_bus import EventBus


class TestEvent:
    def test_event_creation(self):
        e = Event(event_type="TEST", source="test", data={"key": "val"})
        assert e.event_type == "TEST"
        assert e.source == "test"
        assert e.data == {"key": "val"}
        assert e.timestamp > 0

    def test_event_to_dict(self):
        e = Event(event_type="TEST", source="test")
        d = e.to_dict()
        assert d["event_type"] == "TEST"
        assert d["source"] == "test"
        assert "timestamp" in d


class TestEventBus:
    def test_publish_and_subscribe(self):
        bus = EventBus()
        received = []
        bus.subscribe("TEST", lambda e: received.append(e))
        bus.publish(Event(event_type="TEST", source="test"))
        assert len(received) == 1
        assert received[0].event_type == "TEST"

    def test_subscribe_all(self):
        bus = EventBus()
        received = []
        bus.subscribe_all(lambda e: received.append(e))
        bus.publish(Event(event_type="A"))
        bus.publish(Event(event_type="B"))
        assert len(received) == 2

    def test_history(self):
        bus = EventBus()
        bus.publish(Event(event_type="A"))
        bus.publish(Event(event_type="B"))
        history = bus.get_history()
        assert len(history) == 2
        assert history[0]["event_type"] == "A"

    def test_history_filtered(self):
        bus = EventBus()
        bus.publish(Event(event_type="A"))
        bus.publish(Event(event_type="B"))
        bus.publish(Event(event_type="A"))
        history = bus.get_history(event_type="A")
        assert len(history) == 2

    def test_clear(self):
        bus = EventBus()
        bus.subscribe("X", lambda e: None)
        bus.publish(Event(event_type="X"))
        bus.clear()
        assert len(bus.get_history()) == 0


class TestEventType:
    def test_event_types_are_strings(self):
        assert EventType.CHAPTER_COMPLETED == "CHAPTER_COMPLETED"
        assert EventType.SKILL_EXECUTED == "SKILL_EXECUTED"
