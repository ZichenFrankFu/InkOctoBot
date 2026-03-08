"""
Edit Analyzer — analyzes user edits to learn style preferences.

README §2.2.7: Diffs AI-generated text vs user-edited text, categorizes
modifications, and aggregates preferences over time.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from typing import Any

from agents.base_agent import BaseAgent

logger = logging.getLogger("inkoctobot.agents.evaluation.edit_analyzer")


class EditAnalyzer(BaseAgent):
    agent_name = "edit_analyzer"

    def __init__(self, *args, db_path: str = "", **kwargs: Any):
        super().__init__(*args, **kwargs)
        self._db_path = db_path

    async def analyze_edit(
        self,
        original_text: str,
        edited_text: str,
        *,
        chapter_num: int = 0,
    ) -> dict[str, Any]:
        """Analyze the difference between AI output and user-edited text."""
        user_content = (
            "请分析以下AI生成文本与用户修改后文本的差异，"
            "识别修改类型和用户偏好信号。\n\n"
            f"## AI原始文本\n{original_text}\n\n"
            f"## 用户修改后文本\n{edited_text}\n\n"
            "请以JSON格式输出分析结果：\n"
            "```json\n"
            "{\n"
            '  "modifications": [\n'
            '    {"type": "deletion|rewrite|addition|structural",\n'
            '     "description": "修改描述",\n'
            '     "original": "原文片段", "edited": "修改后片段"}\n'
            "  ],\n"
            '  "style_preferences": ["偏好描述"],\n'
            '  "content_preferences": ["内容偏好描述"],\n'
            '  "pacing_preferences": ["节奏偏好描述"]\n'
            "}\n```"
        )
        resp = await self.invoke(
            user_content, temperature=0.3, max_tokens=3000, parse_json=True,
        )
        parsed = resp.raw.get("parsed") or {
            "modifications": [], "style_preferences": [],
            "content_preferences": [], "pacing_preferences": [],
        }

        # Persist preferences to DB
        if self._db_path:
            self._update_preferences(parsed, chapter_num)

        self.emit_event("USER_EDIT_SAVED", {
            "chapter_num": chapter_num,
            "modification_count": len(parsed.get("modifications", [])),
        })
        return parsed

    def _update_preferences(self, analysis: dict[str, Any], chapter_num: int) -> None:
        if not self._db_path:
            return
        with sqlite3.connect(self._db_path) as conn:
            for ptype, key in [
                ("style", "style_preferences"),
                ("content", "content_preferences"),
                ("pacing", "pacing_preferences"),
            ]:
                for pref in analysis.get(key, []):
                    existing = conn.execute(
                        """SELECT pref_id, observation_count, confidence
                           FROM user_style_preferences
                           WHERE project_id=? AND preference_type=? AND description=?""",
                        (self.project_id, ptype, pref),
                    ).fetchone()
                    if existing:
                        new_count = existing[1] + 1
                        new_conf = min(1.0, new_count * 0.15)
                        conn.execute(
                            """UPDATE user_style_preferences
                               SET observation_count=?, confidence=?,
                                   updated_at=CURRENT_TIMESTAMP
                               WHERE pref_id=?""",
                            (new_count, new_conf, existing[0]),
                        )
                    else:
                        conn.execute(
                            """INSERT INTO user_style_preferences
                               (pref_id, project_id, preference_type, description,
                                confidence, observation_count)
                               VALUES (?, ?, ?, ?, 0.15, 1)""",
                            (f"prf_{uuid.uuid4().hex[:12]}",
                             self.project_id, ptype, pref),
                        )
            conn.commit()

    def get_preferences(self, min_confidence: float = 0.3) -> dict[str, list[str]]:
        """Get accumulated user preferences above confidence threshold."""
        if not self._db_path:
            return {}
        with sqlite3.connect(self._db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """SELECT preference_type, description, confidence
                   FROM user_style_preferences
                   WHERE project_id=? AND confidence>=?
                   ORDER BY confidence DESC""",
                (self.project_id, min_confidence),
            ).fetchall()
        result: dict[str, list[str]] = {"style": [], "content": [], "pacing": []}
        for r in rows:
            result[r["preference_type"]].append(r["description"])
        return result
