"""
Memory consolidator — compresses Layer 2 overflow into Truth Files (v2).

When ChapterBuffer exceeds its window budget, the oldest summaries are
processed by an LLM to extract three types of permanent information:
  - permanent_facts          -> ``StatePatch`` entries (current_state)
  - active_foreshadowing     -> ``HookDelta(action='new')`` entries
  - character_state_changes  -> ``EmotionArcEntry`` entries

The Consolidator then bundles them into a ``StorylandStateDeltas`` and applies
atomically via ``StorylandStateStore.apply_deltas``. ChromaDB (Layer 3
semantic store) still receives the chapter summary text for later
similarity queries — vector search and structured truth coexist.

History: v1 wrote ``permanent_facts`` SQL rows and tagged
``episodic_events`` rows with ``foreshadow_status='planted'``. Both
paths were redundant with Truth Files and are removed in v2.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from knowledge.storyland_state.schemas import (
    ChapterSummaryDelta,
    EmotionArcEntry,
    HookDelta,
    HookImportance,
    StatePatch,
    StorylandStateDeltas,
)
from knowledge.storyland_state.store import StorylandStateStore
from llm.base import LLMMessage

logger = logging.getLogger("inkoctobot.knowledge.reader_memory.consolidator")

def _consolidation_prompt() -> str:
    """Read the consolidation prompt through the registry so user can
    override it from 设置 → 提示词. Lazy-imported to avoid a circular
    dep at module load."""
    from reference_pipeline.prompts import render as _render_prompt
    return _render_prompt("pipeline.reader_memory_consolidation")


class ReaderMemoryConsolidator:
    """Compresses old chapter summaries into Truth Files."""

    def __init__(self, router: Any, chapter_buffer: Any,
                 semantic_memory: Any, episodic_timeline: Any,
                 db_path: str):
        self.router = router
        self.chapter_buffer = chapter_buffer
        self.semantic = semantic_memory
        # ``episodic_timeline`` kept for backwards-compat with manager
        # init, but consolidator no longer writes to L4 (foreshadow
        # state lives in pending_hooks).
        self.timeline = episodic_timeline
        self.db_path = db_path

    async def consolidate_if_needed(self, project_id: str) -> list[str]:
        """Check for overflow and consolidate if necessary.

        Returns list of processed summary IDs.
        """
        overflow = self.chapter_buffer.get_overflow_summaries(project_id)
        if not overflow:
            return []

        processed: list[str] = []
        for summary in overflow:
            try:
                await self._consolidate_one(project_id, summary)
                self.chapter_buffer.deactivate_summary(summary["summary_id"])
                processed.append(summary["summary_id"])
                logger.info("Consolidated chapter %d summary", summary["chapter_num"])
            except Exception as e:
                logger.error("Failed to consolidate chapter %d: %s",
                             summary["chapter_num"], e)
        return processed

    async def _consolidate_one(self, project_id: str,
                               summary: dict[str, Any]) -> None:
        chapter_num = summary["chapter_num"]
        summary_text = summary["summary_text"]

        messages = [
            LLMMessage(role="system", content=_consolidation_prompt()),
            LLMMessage(role="user", content=f"第{chapter_num}章摘要:\n{summary_text}"),
        ]
        resp = await self.router.generate(
            agent_role="consolidator", messages=messages,
            temperature=0.3, max_tokens=2000,
        )

        parsed = self._parse_response(resp.content)
        if not parsed:
            # Unparseable JSON: log the raw head and fall back to a
            # semantic-only store. Closes GAP 4 in the observability audit.
            logger.warning(
                "consolidator chapter=%d JSON parse failed; falling back to "
                "raw summary store. raw_head=%r",
                chapter_num, (resp.content or "")[:300],
            )
            self.semantic.store(project_id, summary_text,
                                memory_type="chapter_summary",
                                chapter_num=chapter_num, source="consolidator")
            return

        facts = parsed.get("permanent_facts", []) or []
        foreshadows = parsed.get("active_foreshadowing", []) or []
        state_changes = parsed.get("character_state_changes", []) or []
        logger.info(
            "consolidator chapter=%d facts=%d foreshadowing=%d state_changes=%d",
            chapter_num, len(facts), len(foreshadows), len(state_changes),
        )
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(
                "consolidator chapter=%d extracted=%s",
                chapter_num, json.dumps(parsed, ensure_ascii=False)[:4000],
            )

        # Build a Truth Files delta bundle and apply atomically.
        state_patches = [
            StatePatch(
                subject="叙事",
                predicate="确立事实",
                object=fact,
                valid_from_chapter=chapter_num,
            )
            for fact in facts if isinstance(fact, str) and fact.strip()
        ]
        hook_deltas = [
            HookDelta(
                description=fs.strip(),
                action="new",
                importance=HookImportance.B,
            )
            for fs in foreshadows if isinstance(fs, str) and fs.strip()
        ]
        emotion_entries: list[EmotionArcEntry] = []
        for cs in state_changes:
            if not isinstance(cs, dict):
                continue
            char = (cs.get("character") or "").strip()
            change = (cs.get("change") or "").strip()
            if not char or not change:
                continue
            emotion_entries.append(EmotionArcEntry(
                character=char,
                from_state=cs.get("from_state") or "previous",
                to_state=cs.get("to_state") or "current",
                trigger=change,
            ))

        if state_patches or hook_deltas or emotion_entries:
            try:
                store = StorylandStateStore(project_id=project_id, db_path=self.db_path)
                store.apply_deltas(StorylandStateDeltas(
                    chapter_num=chapter_num,
                    current_state_patches=state_patches,
                    hook_deltas=hook_deltas,
                    emotion_arc_entries=emotion_entries,
                ))
            except Exception as e:
                logger.error(
                    "consolidator apply_deltas failed for chapter=%d: %s",
                    chapter_num, e,
                )

        # v2.1 (MEMORY_VS_TRUTH): the consolidator no longer mirrors
        # facts/foreshadowing into the L3 vector store. Those are
        # canonical in Truth Files (truth_current_state / pending_hooks);
        # double-storing in ChromaDB was the original source of L3↔Truth
        # redundancy.
        #
        # L3 still receives chapter_content via on_chapter_complete in
        # ReaderMemoryManager, which is the correct path for free-text recall.
        # The unparseable-JSON fallback path above also writes a
        # chapter_summary entry to L3 for forensic searchability.

    @staticmethod
    def _parse_response(text: str) -> dict[str, Any] | None:
        m = re.search(r"```json\s*([\s\S]*?)```", text)
        if m:
            try:
                return json.loads(m.group(1))
            except json.JSONDecodeError:
                pass
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return None
