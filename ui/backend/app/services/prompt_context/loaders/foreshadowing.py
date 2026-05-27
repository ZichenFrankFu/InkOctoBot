"""Foreshadowing loader — reads pending_hooks (Truth Files canonical store)."""
from __future__ import annotations

import logging

from ..budget_allocator import LOADER_BUDGETS
from ..loader_protocol import LoaderPlan
from ..utils import clip, section

logger = logging.getLogger("inkoctobot.services.prompt_context.foreshadowing")


_BLOCK = "foreshadowing"
_TITLE = "关联伏笔（本章需埋设或回收的伏笔）"


def plan(
    project_id: str, chapter_id: str = "",
    exclude: set | None = None,
) -> LoaderPlan | None:
    try:
        from ui.backend.app.services import project_store
        from ui.backend.app.services.project_paths import get_db_path

        items = project_store.list_foreshadowing_legacy(get_db_path(), project_id)
    except Exception as e:
        logger.debug("foreshadowing skipped: %s", e)
        return None

    active = [
        it for it in items
        if it.get("status") in ("open", "progressing", "pressured", "near_payoff")
    ]
    if not active:
        return None

    lines: list[str] = []
    for f in active:
        title = (f.get("title") or "").strip()
        content = (f.get("content") or "").strip()
        if not title and not content:
            continue
        origin = f.get("origin_chapter")
        head = f"【{title or '伏笔'}】"
        if origin:
            head = f"{head}（第{origin}章埋设）"
        lines.append(f"- {head}{content}".rstrip())
    if not lines:
        return None

    body = "\n".join(lines)
    cfg = LOADER_BUDGETS[_BLOCK]
    overhead = len(_TITLE) + 6
    natural = overhead + len(body)

    def render(budget: int) -> str:
        return section(_TITLE, clip(body, max(0, budget - overhead)))

    return LoaderPlan(
        block_id=_BLOCK,
        natural_length=natural,
        minimum=cfg["min"], target=cfg["target"], maximum=cfg["max"],
        priority_tier=cfg["tier"], render=render,
    )


def load(project_id: str, chapter_id: str = "") -> str:
    p = plan(project_id, chapter_id)
    return p.render(p.target) if p else ""
