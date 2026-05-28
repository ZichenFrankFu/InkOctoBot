"""
Memory Manager — coordinates all four memory layers.

Provides a unified interface for agents to read from and write to
the memory system.  Handles cross-layer operations like chapter-end
updates and compression cascades.
"""
from __future__ import annotations

import logging
from typing import Any

from knowledge.reader_memory.immediate import ImmediateContext, SceneContext
from knowledge.reader_memory.chapter_buffer import ChapterBuffer
from knowledge.reader_memory.semantic_store import SemanticMemory
from knowledge.reader_memory.episodic_timeline import EpisodicTimeline
from knowledge.reader_memory.consolidator import ReaderMemoryConsolidator
from knowledge.reader_memory.knowledge_isolation import KnowledgeIsolationEngine, FilteredWorldView

logger = logging.getLogger("inkoctobot.knowledge.reader_memory.manager")


class ReaderMemoryManager:
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
        self._consolidator: ReaderMemoryConsolidator | None = None
        if router:
            self._consolidator = ReaderMemoryConsolidator(
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
        """Open foreshadowing read from Truth Files' ``pending_hooks``.

        Returns dicts shaped for legacy consumers:
          ``{description, chapter_num, status, importance, hook_id, foreshadow_status}``
        where ``chapter_num`` is the origin_chapter (where it was planted)
        and ``foreshadow_status`` is an alias of ``status`` (kept so old
        formatters that read ``f['foreshadow_status']`` keep working).
        """
        import sqlite3
        try:
            with sqlite3.connect(self.db_path) as c:
                c.row_factory = sqlite3.Row
                rows = c.execute(
                    "SELECT hook_id, description, origin_chapter AS chapter_num, "
                    "status, importance, expected_payoff_chapter "
                    "FROM pending_hooks "
                    "WHERE project_id=? AND status IN "
                    "('open','progressing','pressured','near_payoff') "
                    "ORDER BY origin_chapter",
                    (self._project_id,),
                ).fetchall()
        except sqlite3.OperationalError:
            return []
        out: list[dict] = []
        for r in rows:
            d = dict(r)
            # Legacy alias — some callers read d['foreshadow_status'].
            d["foreshadow_status"] = d["status"]
            out.append(d)
        return out

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

    # ── Fallback: editor content when memory layers are empty ──

    def has_memory_content(self) -> bool:
        """Check if any memory layer has meaningful content."""
        l1 = self.immediate.get_context_text(include_previous=True)
        if l1:
            return True
        l2 = self.chapter_buffer.get_buffer_text(self._project_id)
        if l2:
            return True
        return False

    def build_fallback_from_editor(self, editor_volumes: list[dict],
                                    current_chapter_id: str = "") -> str:
        """Build memory-like context from saved editor chapter content.

        Used when no pipeline has been run yet, so all memory layers are empty.
        Extracts summaries from previous chapters' existing content.
        """
        parts: list[str] = []
        all_chapters: list[dict] = []
        for v in editor_volumes:
            for c in v.get("chapters", []):
                all_chapters.append(c)

        # Sort by order
        all_chapters.sort(key=lambda c: c.get("order", 0))

        # Find current chapter index
        current_idx = -1
        for i, c in enumerate(all_chapters):
            if c.get("id") == current_chapter_id:
                current_idx = i
                break

        # Build context from previous chapters that have content
        prev_summaries: list[str] = []
        for i, c in enumerate(all_chapters):
            if i >= current_idx and current_idx >= 0:
                break
            text = c.get("content", "").strip()
            synopsis = c.get("synopsis", "").strip()
            if not text and not synopsis:
                continue
            title = c.get("title", f"第{i + 1}章")
            # Use synopsis if available, otherwise truncate content
            if synopsis:
                prev_summaries.append(f"第{c.get('order', i + 1)}章 {title}：{synopsis}")
            elif text:
                # Take first ~200 chars as a rough summary
                snippet = text[:200].replace("\n", " ")
                if len(text) > 200:
                    snippet += "..."
                prev_summaries.append(f"第{c.get('order', i + 1)}章 {title}：{snippet}")

        if prev_summaries:
            parts.append("[已有章节内容（编辑器 Fallback）]")
            parts.extend(prev_summaries[-10:])  # Keep last 10 chapters

        # If current chapter has content, add as immediate context
        if current_idx >= 0:
            cur = all_chapters[current_idx]
            cur_text = cur.get("content", "").strip()
            if cur_text:
                # Last 800 chars as "current scene"
                snippet = cur_text[-800:]
                if len(cur_text) > 800:
                    snippet = "..." + snippet
                parts.append(f"\n[当前章节已有内容]\n{snippet}")

        return "\n\n".join(parts)

    def get_stats(self) -> dict[str, Any]:
        return {
            "layer1": self.immediate.to_dict(),
            "layer2_active": len(self.chapter_buffer.get_active_summaries(self._project_id)),
            "layer3_count": self.semantic.count(),
            "layer4": self.timeline.get_event_stats(self._project_id),
        }
