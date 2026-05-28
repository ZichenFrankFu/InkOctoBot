"""User writing-preference loader (wraps services.style_preferences)."""
from __future__ import annotations

import logging

from ..budget_allocator import LOADER_BUDGETS
from ..loader_protocol import LoaderPlan
from ..utils import clip, section

logger = logging.getLogger("inkoctobot.services.prompt_context.user_preferences")


_BLOCK = "user_preferences"
_TITLE = "用户写作偏好（从历史修改中学习）"


def plan(project_id: str, db_path: str, exclude: set | None = None) -> LoaderPlan | None:
    try:
        from ui.backend.app.services import load_user_style_preferences
        txt = (load_user_style_preferences(project_id, db_path) or "").strip()
    except Exception as e:
        logger.debug("user preferences skipped: %s", e)
        return None
    if not txt:
        return None

    cfg = LOADER_BUDGETS[_BLOCK]
    # natural ≈ section header + body
    natural = len(_TITLE) + 6 + len(txt)

    def render(budget: int) -> str:
        return section(_TITLE, clip(txt, max(0, budget - len(_TITLE) - 6)))

    return LoaderPlan(
        block_id=_BLOCK,
        natural_length=natural,
        minimum=cfg["min"], target=cfg["target"], maximum=cfg["max"],
        priority_tier=cfg["tier"], render=render,
    )


def load(project_id: str, db_path: str) -> str:
    p = plan(project_id, db_path)
    return p.render(p.target) if p else ""
