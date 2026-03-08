"""
Knowledge Isolation Engine.

Ensures Actor Agents only access information their character
"should know at this moment", preventing context leaking.

Input:  character_name + current chapter/scene + Scene Director's knowledge_boundary
Output: filtered_world_view for injection into Actor Agent's prompt.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("inkoctobot.rag.memory.knowledge_isolation")


@dataclass
class FilteredWorldView:
    """What a character knows/believes at a given moment."""
    character_name: str
    chapter_num: int
    known_true: list[str] = field(default_factory=list)
    known_false: list[dict[str, str]] = field(default_factory=list)
    explicitly_unknown: list[str] = field(default_factory=list)

    def to_prompt(self) -> str:
        parts = [f"## {self.character_name} 的认知状态（第{self.chapter_num}章）"]
        if self.known_true:
            parts.append("\n### 已知事实")
            for fact in self.known_true:
                parts.append(f"- {fact}")
        if self.known_false:
            parts.append("\n### 错误认知（角色相信但不是事实）")
            for item in self.known_false:
                parts.append(f"- 角色相信: {item.get('believed', '')}")
        if self.explicitly_unknown:
            parts.append(
                f"\n### 禁止信息\n"
                f"你（{self.character_name}）目前不知道以下信息，"
                f"在表演中不得暗示或提及："
            )
            for unknown in self.explicitly_unknown:
                parts.append(f"- {unknown}")
        return "\n".join(parts)


class KnowledgeIsolationEngine:
    """Filters world information based on character knowledge state."""

    def __init__(self, db_path: str, semantic_memory: Any):
        self.db_path = db_path
        self.semantic = semantic_memory

    def _conn(self) -> sqlite3.Connection:
        c = sqlite3.connect(self.db_path)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys=ON")
        return c

    def build_world_view(
        self,
        project_id: str,
        character_name: str,
        chapter_num: int,
        scene_context: str = "",
        knowledge_boundary: dict[str, Any] | None = None,
    ) -> FilteredWorldView:
        """Build the filtered world view for a character at a given moment."""
        view = FilteredWorldView(
            character_name=character_name,
            chapter_num=chapter_num,
        )

        # 1. Retrieve relevant memories from Layer 3
        query = f"{character_name} {scene_context}" if scene_context else character_name
        memories = self.semantic.query(query, project_id, n_results=20)

        # 2. Check each memory against information_events
        info_map = self._get_character_info(project_id, character_name)

        for mem in memories:
            text = mem.get("text", "")
            fact_key = text[:100]  # use first 100 chars as rough key

            state = info_map.get(fact_key, {}).get("knowledge_state", "check")

            if state == "known_true":
                view.known_true.append(text)
            elif state == "known_false":
                believed = info_map[fact_key].get("believed_value", text)
                view.known_false.append({"believed": believed, "truth": text})
            elif state == "unknown":
                view.explicitly_unknown.append(text)
            else:
                # Not in info_events — apply knowledge boundary rules
                if knowledge_boundary:
                    restricted = knowledge_boundary.get("restricted_facts", [])
                    if any(r.lower() in text.lower() for r in restricted):
                        view.explicitly_unknown.append(text)
                        continue
                view.known_true.append(text)

        # 3. Apply explicit Scene Director restrictions
        if knowledge_boundary:
            for restriction in knowledge_boundary.get("must_not_know", []):
                if restriction not in view.explicitly_unknown:
                    view.explicitly_unknown.append(restriction)

        return view

    def _get_character_info(self, project_id: str, character_name: str) -> dict[str, dict]:
        """Get all information events for a character."""
        with self._conn() as c:
            rows = c.execute(
                """SELECT * FROM information_events
                   WHERE project_id=? AND character_name=?""",
                (project_id, character_name),
            ).fetchall()
        return {r["fact_key"]: dict(r) for r in rows}

    def record_information_event(
        self,
        project_id: str,
        character_name: str,
        fact_key: str,
        knowledge_state: str,
        *,
        believed_value: str = "",
        true_value: str = "",
        source_chapter: int = 0,
        source_description: str = "",
    ) -> str:
        """Record that a character learned (or was misled about) something."""
        import uuid
        info_id = f"inf_{uuid.uuid4().hex[:12]}"
        with self._conn() as c:
            c.execute(
                """INSERT INTO information_events
                   (info_id, project_id, character_name, fact_key, knowledge_state,
                    believed_value, true_value, source_chapter, source_description)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (info_id, project_id, character_name, fact_key, knowledge_state,
                 believed_value, true_value, source_chapter, source_description),
            )
            c.commit()
        return info_id

    def get_character_knowledge_summary(
        self, project_id: str, character_name: str,
    ) -> dict[str, Any]:
        """Summary of what a character knows/doesn't know."""
        with self._conn() as c:
            rows = c.execute(
                """SELECT knowledge_state, COUNT(*) as cnt
                   FROM information_events
                   WHERE project_id=? AND character_name=?
                   GROUP BY knowledge_state""",
                (project_id, character_name),
            ).fetchall()
        return {r["knowledge_state"]: r["cnt"] for r in rows}
