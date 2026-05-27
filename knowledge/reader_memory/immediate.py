"""
Layer 1: Immediate Context.

Lives entirely in the LLM context window (4-8K tokens).
Stores the current scene text + previous scene for direct working context.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

logger = logging.getLogger("inkoctobot.knowledge.reader_memory.immediate")


@dataclass
class SceneContext:
    """A single scene's content for immediate context."""
    scene_index: int = 0
    scene_text: str = ""
    characters: list[str] = field(default_factory=list)
    location: str = ""
    time_marker: str = ""


class ImmediateContext:
    """
    Layer 1 — maintains current + previous scene in memory.

    Automatically rotates when a new scene starts: current becomes previous,
    new scene becomes current.
    """

    def __init__(self, max_tokens: int = 6000):
        self.max_tokens = max_tokens
        self.current_scene: SceneContext | None = None
        self.previous_scene: SceneContext | None = None
        self._chapter_num: int = 0

    def set_chapter(self, chapter_num: int) -> None:
        self._chapter_num = chapter_num
        self.current_scene = None
        self.previous_scene = None

    def advance_scene(self, scene: SceneContext) -> None:
        """Push a new scene: current becomes previous."""
        self.previous_scene = self.current_scene
        self.current_scene = scene

    def update_current(self, text: str) -> None:
        """Update the current scene's text (e.g. after actor output)."""
        if self.current_scene:
            self.current_scene.scene_text = text

    def get_context_text(self, *, include_previous: bool = True) -> str:
        """Build the text that goes into the LLM context window."""
        parts: list[str] = []
        if include_previous and self.previous_scene and self.previous_scene.scene_text:
            parts.append(f"[前一场景]\n{self._truncate(self.previous_scene.scene_text)}")
        if self.current_scene and self.current_scene.scene_text:
            parts.append(f"[当前场景]\n{self.current_scene.scene_text}")
        return "\n\n".join(parts)

    def get_for_actor(self, character_name: str) -> str:
        """Get context for a specific actor (may be filtered by knowledge isolation)."""
        if not self.current_scene:
            return ""
        parts = [f"[当前场景 — 第{self._chapter_num}章]"]
        if self.current_scene.location:
            parts.append(f"地点: {self.current_scene.location}")
        if self.current_scene.time_marker:
            parts.append(f"时间: {self.current_scene.time_marker}")
        parts.append(f"在场人物: {', '.join(self.current_scene.characters)}")
        if self.current_scene.scene_text:
            parts.append(f"\n{self.current_scene.scene_text}")
        return "\n".join(parts)

    def _truncate(self, text: str) -> str:
        """Rough truncation to stay within token budget."""
        char_limit = self.max_tokens * 2  # ~2 chars per token for Chinese
        if len(text) <= char_limit:
            return text
        return text[:char_limit] + "\n...[已截断]"

    def to_dict(self) -> dict:
        return {
            "chapter_num": self._chapter_num,
            "has_current": self.current_scene is not None,
            "has_previous": self.previous_scene is not None,
        }
