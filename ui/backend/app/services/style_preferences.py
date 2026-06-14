"""User writing-style preferences loader.

EditAnalyzer learns from user edits and writes preferences into
``user_style_preferences`` table. This module loads them into a
text block ready to drop into a prompt.
"""
from __future__ import annotations

import logging
import sqlite3

logger = logging.getLogger("inkoctobot.services.style_preferences")


def load_user_style_preferences(project_id: str, db_path: str) -> str:
    """Load accumulated user style preferences from the EditAnalyzer feedback loop.

    Returns a markdown-style block ready to inject into a prompt, or "" when
    there's nothing useful yet. All DB / parsing errors degrade gracefully
    to "" so generation never breaks on a feedback hiccup.

    Only CONFIRMED preferences are injected (用户偏好·机制4 +
    LLM交互·机制2): preferences learned by the preference_analyzer land
    as ``is_confirmed=0`` pending rows and stay out of prompts until
    the user approves them.
    """
    try:
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """SELECT preference_type, description, confidence
                   FROM user_style_preferences
                   WHERE project_id=? AND is_confirmed=1
                   ORDER BY confidence DESC LIMIT 20""",
                (project_id,),
            ).fetchall()
        if not rows:
            return ""
        parts = ["[用户写作偏好（从历史修改中学习）]"]
        by_type: dict[str, list[str]] = {}
        for r in rows:
            by_type.setdefault(r["preference_type"], []).append(
                f"- {r['description']} (置信度: {r['confidence']:.0%})"
            )
        type_labels = {"style": "风格偏好", "content": "内容偏好", "pacing": "节奏偏好"}
        for ptype, items in by_type.items():
            parts.append(f"\n### {type_labels.get(ptype, ptype)}")
            parts.extend(items[:8])
        return "\n".join(parts)
    except Exception as e:
        logger.debug("Load user preferences skipped: %s", e)
        return ""
