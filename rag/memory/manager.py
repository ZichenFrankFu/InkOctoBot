"""
Memory Manager — coordinates all four memory layers.

Provides a unified interface for agents to read from and write to
the memory system.  Handles cross-layer operations like chapter-end
updates and compression cascades.
"""
from __future__ import annotations

import logging
from typing import Any

from rag.memory.immediate import ImmediateContext, SceneContext
from rag.memory.chapter_buffer import ChapterBuffer
from rag.memory.semantic_store import SemanticMemory
from rag.memory.episodic_timeline import EpisodicTimeline
from rag.memory.consolidator import MemoryConsolidator
from rag.memory.knowledge_isolation import KnowledgeIsolationEngine, FilteredWorldView

logger = logging.getLogger("inkoctobot.rag.memory.manager")


class MemoryManager:
    """Unified coordinator for the 4-layer memory system."""

    def __init__(
        self,
        db_path: str,
        chromadb_dir: str | None = None,
        router: Any = None,
    ):
        self.db_path = db_path
        self.immediate = ImmediateContext()
        self.chapter_buffer = ChapterBuffer(db_path)
        self.semantic = SemanticMemory(persist_dir=chromadb_dir)
        self.timeline = EpisodicTimeline(db_path)
        self.isolation = KnowledgeIsolationEngine(db_path, self.semantic)
        self._consolidator: MemoryConsolidator | None = None
        if router:
            self._consolidator = MemoryConsolidator(
                router, self.chapter_buffer, self.semantic, self.timeline, db_path,
            )
        self._project_id: str = ""

    def set_project(self, project_id: str) -> None:
        self._project_id = project_id

    # ── Layer 1: Scene-level operations ──

    def start_scene(self, scene: SceneContext) -> None:
        self.immediate.advance_scene(scene)

    def update_scene_text(self, text: str) -> None:
        self.immediate.update_current(text)

    def set_chapter(self, chapter_num: int) -> None:
        self.immediate.set_chapter(chapter_num)

    # ── Layer 2: Chapter-level operations ──

    def add_chapter_summary(
        self, chapter_num: int, summary: str,
        key_events: list[str] | None = None,
        character_states: dict[str, str] | None = None,
    ) -> str:
        return self.chapter_buffer.add_summary(
            self._project_id, chapter_num, summary, key_events, character_states,
        )

    # ── Layer 3: Semantic queries ──

    def search_memory(self, query: str, n_results: int = 5,
                      memory_type: str | None = None) -> list[dict]:
        return self.semantic.query(query, self._project_id,
                                   n_results=n_results, memory_type=memory_type)

    def store_memory(self, content: str, memory_type: str = "general",
                     chapter_num: int = 0, **kwargs: Any) -> str:
        return self.semantic.store(self._project_id, content,
                                   memory_type=memory_type,
                                   chapter_num=chapter_num, **kwargs)

    # ── Layer 4: Timeline queries ──

    def add_event(self, chapter_num: int, event_type: str, description: str,
                  **kwargs: Any) -> str:
        return self.timeline.add_event(
            self._project_id, chapter_num, event_type, description, **kwargs,
        )

    def get_unresolved_foreshadowing(self) -> list[dict]:
        return self.timeline.get_unresolved_foreshadowing(self._project_id)

    def get_timeline(self, from_chapter: int = 0, to_chapter: int = 9999) -> list[dict]:
        return self.timeline.get_timeline(
            self._project_id, from_chapter=from_chapter, to_chapter=to_chapter,
        )

    # ── Knowledge isolation ──

    def get_character_view(
        self, character_name: str, chapter_num: int,
        scene_context: str = "",
        knowledge_boundary: dict[str, Any] | None = None,
    ) -> FilteredWorldView:
        return self.isolation.build_world_view(
            self._project_id, character_name, chapter_num,
            scene_context, knowledge_boundary,
        )

    # ── Cross-layer: chapter-end processing ──

    async def on_chapter_complete(
        self,
        chapter_num: int,
        chapter_text: str,
        summary: str,
        key_events: list[str] | None = None,
        character_states: dict[str, str] | None = None,
        events: list[dict[str, Any]] | None = None,
    ) -> None:
        """End-of-chapter hook: update all layers."""
        # L2: Add summary
        self.add_chapter_summary(chapter_num, summary, key_events, character_states)

        # L3: Store chapter content as searchable memory
        self.store_memory(
            chapter_text[:2000],
            memory_type="chapter_content",
            chapter_num=chapter_num,
        )

        # L4: Add extracted events
        if events:
            for evt in events:
                self.add_event(chapter_num, **evt)

        # Compression check
        if self._consolidator:
            await self._consolidator.consolidate_if_needed(self._project_id)

        logger.info("Chapter %d memory update complete", chapter_num)

    # ── Context assembly for agents ──

    def get_context_for_scene_director(self, chapter_num: int) -> str:
        """Full context for Scene Director: L1 previous + L2 full + L3 + L4."""
        parts = []
        l1 = self.immediate.get_context_text(include_previous=True)
        if l1:
            parts.append(l1)
        l2 = self.chapter_buffer.get_buffer_text(self._project_id)
        if l2:
            parts.append(l2)
        foreshadowing = self.get_unresolved_foreshadowing()
        if foreshadowing:
            parts.append("[未回收伏笔]")
            for f in foreshadowing[:10]:
                parts.append(f"- 第{f['chapter_num']}章: {f['description']}")
        return "\n\n".join(parts)

    def get_context_for_actor(self, character_name: str, chapter_num: int,
                               scene_context: str = "",
                               knowledge_boundary: dict | None = None) -> str:
        """Filtered context for Actor Agent: L1 current only + knowledge-isolated."""
        parts = []
        l1 = self.immediate.get_for_actor(character_name)
        if l1:
            parts.append(l1)
        view = self.get_character_view(
            character_name, chapter_num, scene_context, knowledge_boundary,
        )
        parts.append(view.to_prompt())
        return "\n\n".join(parts)

    def get_context_for_editor(self) -> str:
        """Full context for Editor-Writer: L1 full + L2 full."""
        parts = []
        l1 = self.immediate.get_context_text(include_previous=True)
        if l1:
            parts.append(l1)
        l2 = self.chapter_buffer.get_buffer_text(self._project_id)
        if l2:
            parts.append(l2)
        return "\n\n".join(parts)

    def get_context_for_evaluator(self, chapter_num: int) -> str:
        """Full context for Evaluator: L1 + L2 + L3 + L4."""
        parts = [self.get_context_for_editor()]
        foreshadowing = self.get_unresolved_foreshadowing()
        if foreshadowing:
            parts.append("[伏笔追踪]")
            for f in foreshadowing:
                parts.append(f"- 第{f['chapter_num']}章: {f['description']} [{f['foreshadow_status']}]")
        return "\n\n".join(parts)

    def get_stats(self) -> dict[str, Any]:
        return {
            "layer1": self.immediate.to_dict(),
            "layer2_active": len(self.chapter_buffer.get_active_summaries(self._project_id)),
            "layer3_count": self.semantic.count(),
            "layer4": self.timeline.get_event_stats(self._project_id),
        }
