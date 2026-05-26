"""User writing-preference loader (wraps services.style_preferences)."""
from __future__ import annotations

import logging

from ..budgets import BUDGETS
from ..utils import clip, section

logger = logging.getLogger("inkoctobot.services.prompt_context.user_preferences")


def load(project_id: str, db_path: str) -> str:
    try:
        from ui.backend.app.services import load_user_style_preferences

        txt = load_user_style_preferences(project_id, db_path)
        if not txt:
            return ""
        return section(
            "用户写作偏好（从历史修改中学习）",
            clip(txt, BUDGETS["user_preferences"]),
        )
    except Exception as e:
        logger.debug("user preferences skipped: %s", e)
        return ""
