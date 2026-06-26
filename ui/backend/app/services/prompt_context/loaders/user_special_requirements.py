"""User special requirements loader.

Free-text per-chapter requirements the user wants the Writer to honour
for the chapter being generated. Replaces the v3 ``style_calibration``
prompt block — calibration's slider-based style tuning has been
removed from the prompt chain (the calibration UI / agent still own
that surface; it just doesn't inject into chapter prompts any more).

Data source: ``chapters.special_requirements`` column (added in v3.1).
Empty value → ``plan()`` returns ``None`` and the loader is inactive.
"""
from __future__ import annotations

import logging

from ..budget_allocator import LOADER_BUDGETS
from ..chapter_fields import load_chapter_fields
from ..loader_protocol import LoaderPlan
from ..utils import clip, section

logger = logging.getLogger(
    "inkoctobot.services.prompt_context.user_special_requirements"
)


_BLOCK = "user_special_requirements"
# UI 侧字段从「用户特别要求」改名为「创作备注」，prompt 注入段标题随
# 之同步，避免两端用词不一致。loader 名 / 数据列名保持 special_requirements
# / user_special_requirements，避免库迁移。
_TITLE = "创作备注"


def plan(
    project_id: str, chapter_id: str = "",
    exclude: set | None = None,
) -> LoaderPlan | None:
    if not chapter_id:
        return None
    try:
        text = (
            load_chapter_fields(project_id, chapter_id).get("special_requirements")
            or ""
        ).strip()
    except Exception as e:
        logger.debug("special requirements lookup failed: %s", e)
        return None
    if not text:
        return None

    cfg = LOADER_BUDGETS[_BLOCK]
    natural = len(_TITLE) + 6 + len(text)

    def render(budget: int) -> str:
        return section(_TITLE, clip(text, max(0, budget - len(_TITLE) - 6)))

    return LoaderPlan(
        block_id=_BLOCK,
        natural_length=natural,
        minimum=cfg["min"], target=cfg["target"], maximum=cfg["max"],
        priority_tier=cfg["tier"], render=render,
    )


def load(project_id: str, chapter_id: str = "") -> str:
    p = plan(project_id, chapter_id)
    return p.render(p.target) if p else ""
