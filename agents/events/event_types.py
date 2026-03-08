"""
Event types and data classes for the agent event system.

README §2.6: EventBus + AgentTrigger architecture.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class EventType(str, Enum):
    CHAPTER_COMPLETED = "CHAPTER_COMPLETED"
    USER_EDIT_SAVED = "USER_EDIT_SAVED"
    WORLDBOOK_UPDATED = "WORLDBOOK_UPDATED"
    CHARACTER_UPDATED = "CHARACTER_UPDATED"
    CHAPTER_PLAN_SUBMITTED = "CHAPTER_PLAN_SUBMITTED"
    GENERATION_STEP_COMPLETED = "GENERATION_STEP_COMPLETED"
    CONSTRAINT_ADDED = "CONSTRAINT_ADDED"
    EVALUATION_COMPLETED = "EVALUATION_COMPLETED"
    MEMORY_CONSOLIDATED = "MEMORY_CONSOLIDATED"
    PROJECT_CREATED = "PROJECT_CREATED"


@dataclass
class Event:
    """A single event published to the EventBus."""
    event_type: str
    source: str = ""
    project_id: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_type": self.event_type,
            "source": self.source,
            "project_id": self.project_id,
            "data": self.data,
            "timestamp": self.timestamp,
        }


@dataclass
class AgentSuggestion:
    """A proactive suggestion from an agent, pushed to the user."""
    agent_name: str
    title: str
    content: str
    severity: str = "info"       # "info" | "warning" | "suggestion"
    event_type: str = ""
    project_id: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_name": self.agent_name,
            "title": self.title,
            "content": self.content,
            "severity": self.severity,
            "event_type": self.event_type,
            "project_id": self.project_id,
            "data": self.data,
            "timestamp": self.timestamp,
        }
