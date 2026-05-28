"""
Layer 2: Chapter Buffer.

Maintains structured summaries of recent chapters (5-10) in the LLM
context window (2-4K tokens). When chapters exceed the window budget,
the oldest summaries are compressed and demoted to Layer 3/4.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
import uuid
from typing import Any

logger = logging.getLogger("inkoctobot.knowledge.reader_memory.chapter_buffer")


class ChapterBuffer:
    """
    Layer 2 — recent chapter summaries kept in context window.

    Summaries are stored in SQLite (chapter_summaries table) and
    the active window is loaded on demand.
    """

    def __init__(self, db_path: str, *, max_chapters: int = 10, max_tokens: int = 3000):
        self.db_path = db_path
        self.max_chapters = max_chapters
        self.max_tokens = max_tokens
        self._local = threading.local()

    def _conn(self) -> sqlite3.Connection:
        c = getattr(self._local, "conn", None)
        if c is None:
            c = sqlite3.connect(self.db_path, check_same_thread=False)
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA foreign_keys=ON")
            self._local.conn = c
        return c

    def add_summary(
        self,
        project_id: str,
        chapter_num: int,
        summary_text: str,
        key_events: list[str] | None = None,
        character_states: dict[str, str] | None = None,
    ) -> str:
        sid = f"sum_{uuid.uuid4().hex[:12]}"
        with self._conn() as c:
            c.execute(
                """INSERT OR REPLACE INTO chapter_summaries
                   (summary_id, project_id, chapter_num, summary_text,
                    key_events_json, character_states_json, is_active)
                   VALUES (?, ?, ?, ?, ?, ?, 1)""",
                (sid, project_id, chapter_num, summary_text,
                 json.dumps(key_events or [], ensure_ascii=False),
                 json.dumps(character_states or {}, ensure_ascii=False)),
            )
            c.commit()
        return sid

    def get_active_summaries(self, project_id: str) -> list[dict[str, Any]]:
        """Get all active (in-window) chapter summaries, ordered by chapter number."""
        with self._conn() as c:
            rows = c.execute(
                """SELECT * FROM chapter_summaries
                   WHERE project_id=? AND is_active=1
                   ORDER BY chapter_num""",
                (project_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_overflow_summaries(self, project_id: str) -> list[dict[str, Any]]:
        """Get active summaries that exceed the window budget."""
        active = self.get_active_summaries(project_id)
        if len(active) <= self.max_chapters:
            return []
        return active[: len(active) - self.max_chapters]

    def deactivate_summary(self, summary_id: str) -> None:
        """Mark a summary as compressed/demoted (no longer in active window)."""
        with self._conn() as c:
            c.execute(
                "UPDATE chapter_summaries SET is_active=0 WHERE summary_id=?",
                (summary_id,),
            )
            c.commit()

    def get_buffer_text(self, project_id: str) -> str:
        """Build the text block for injection into LLM context."""
        summaries = self.get_active_summaries(project_id)
        if not summaries:
            return ""
        parts = ["[近期章节摘要]"]
        for s in summaries:
            parts.append(f"\n第{s['chapter_num']}章:")
            parts.append(s["summary_text"])
            events = json.loads(s["key_events_json"]) if s["key_events_json"] else []
            if events:
                parts.append("关键事件: " + "; ".join(events))
        text = "\n".join(parts)
        char_limit = self.max_tokens * 2
        if len(text) > char_limit:
            text = text[:char_limit] + "\n...[已截断]"
        return text

    def needs_compression(self, project_id: str) -> bool:
        active = self.get_active_summaries(project_id)
        return len(active) > self.max_chapters
