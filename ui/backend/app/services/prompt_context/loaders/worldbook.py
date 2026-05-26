"""Worldbook (世界书) loader."""
from __future__ import annotations

import logging

from ..budgets import BUDGETS
from ..utils import clip, section

logger = logging.getLogger("inkoctobot.services.prompt_context.worldbook")


def load(project_id: str, exclude: set | None = None) -> str:
    """Collect the project's worldbook entries."""
    try:
        from ui.backend.app.services import project_store
        from ui.backend.app.services.project_paths import get_db_path

        rows = project_store.list_worldbook(get_db_path(), project_id)
        if not rows:
            return ""
        entries: list[str] = []
        for e in rows:
            eid = str(e.get("id") or e.get("title") or "")
            if exclude and eid in exclude:
                continue
            title = (e.get("title") or "").strip()
            content = (e.get("content") or "").strip()
            if not title and not content:
                continue
            cat = (e.get("category") or "").strip()
            head = f"【{title}】" + (f"（{cat}）" if cat else "")
            entries.append(f"{head} {content}".strip())
        if not entries:
            return ""
        return section("世界观设定（世界书）", clip("\n".join(entries), BUDGETS["worldbook"]))
    except Exception as e:
        logger.debug("worldbook skipped: %s", e)
        return ""
