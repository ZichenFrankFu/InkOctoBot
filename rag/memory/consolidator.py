"""
Memory consolidator — compresses Layer 2 overflow into Layer 3/4.

When ChapterBuffer exceeds its window budget, the oldest summaries
are processed by LLM to extract three types of permanent information:
  - permanent_facts: irreversible facts
  - active_foreshadowing: unresolved foreshadowing
  - character_state_changes: character state transitions
These are written to Layer 3 (Semantic Memory) and Layer 4 (Episodic Timeline),
and the original summary is deactivated from Layer 2.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from agents.model_providers.base import LLMMessage

logger = logging.getLogger("inkoctobot.rag.memory.consolidator")

_CONSOLIDATION_PROMPT = """\
你是一个小说记忆压缩引擎。给定一个章节摘要，提取以下三类永久信息：

1. permanent_facts: 不可逆的事实（如"张远在第5章觉醒了灵根"）
2. active_foreshadowing: 尚未回收的伏笔（如"李清漪在第7章提到的'那个人'身份未揭示"）
3. character_state_changes: 角色状态变化（如"张远对宗门的信任从中立变为怀疑"）

请以JSON格式输出：
```json
{
  "permanent_facts": ["...", "..."],
  "active_foreshadowing": ["...", "..."],
  "character_state_changes": [
    {"character": "角色名", "change": "变化描述", "from_state": "原状态", "to_state": "新状态"}
  ]
}
```
"""


class MemoryConsolidator:
    """Compresses old chapter summaries into permanent memory."""

    def __init__(self, router: Any, chapter_buffer: Any,
                 semantic_memory: Any, episodic_timeline: Any, db_path: str):
        self.router = router
        self.chapter_buffer = chapter_buffer
        self.semantic = semantic_memory
        self.timeline = episodic_timeline
        self.db_path = db_path

    async def consolidate_if_needed(self, project_id: str) -> list[str]:
        """Check for overflow and consolidate if necessary. Returns list of processed summary IDs."""
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

    async def _consolidate_one(self, project_id: str, summary: dict[str, Any]) -> None:
        chapter_num = summary["chapter_num"]
        summary_text = summary["summary_text"]

        messages = [
            LLMMessage(role="system", content=_CONSOLIDATION_PROMPT),
            LLMMessage(role="user", content=f"第{chapter_num}章摘要:\n{summary_text}"),
        ]
        resp = await self.router.generate(
            agent_role="consolidator", messages=messages,
            temperature=0.3, max_tokens=2000,
        )

        parsed = self._parse_response(resp.content)
        if not parsed:
            self.semantic.store(project_id, summary_text,
                                memory_type="chapter_summary",
                                chapter_num=chapter_num, source="consolidator")
            return

        for fact in parsed.get("permanent_facts", []):
            self.semantic.store_permanent_fact(project_id, fact, chapter_num)
            import sqlite3, uuid
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    """INSERT INTO permanent_facts (fact_id, project_id, fact_type, content, source_chapter)
                       VALUES (?, ?, 'permanent', ?, ?)""",
                    (f"pf_{uuid.uuid4().hex[:12]}", project_id, fact, chapter_num),
                )
                conn.commit()

        for fs in parsed.get("active_foreshadowing", []):
            self.timeline.add_event(
                project_id, chapter_num, "foreshadowing", fs,
                foreshadow_status="planted", importance=4,
            )
            self.semantic.store(project_id, fs, memory_type="foreshadowing",
                                chapter_num=chapter_num, source="consolidator")

        for cs in parsed.get("character_state_changes", []):
            char = cs.get("character", "")
            change = cs.get("change", "")
            if char and change:
                self.semantic.store_character_state(project_id, char, change, chapter_num)
                self.timeline.add_event(
                    project_id, chapter_num, "character_change", f"{char}: {change}",
                    characters=[char], importance=3,
                )

    @staticmethod
    def _parse_response(text: str) -> dict[str, Any] | None:
        import re
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
