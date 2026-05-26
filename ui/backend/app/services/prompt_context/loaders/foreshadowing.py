"""Foreshadowing loader — reads pending_hooks (Truth Files canonical store).

Previously read ``data/foreshadowing/<pid>.json``; that file is gone
in v2. See docs/SCHEMA_REDESIGN.md for the redesign rationale.
"""
from __future__ import annotations

import logging

from ..budgets import BUDGETS
from ..utils import clip, section

logger = logging.getLogger("inkoctobot.services.prompt_context.foreshadowing")


def load(project_id: str, chapter_id: str = "") -> str:
    """Inject the project's open hooks as foreshadowing reminders.

    ``chapter_id`` is accepted for API compatibility but ignored — hooks
    don't have per-chapter binding the way the old user-managed table
    did; instead, all OPEN/PROGRESSING/PRESSURED/NEAR_PAYOFF hooks are
    relevant context for any chapter being generated.
    """
    try:
        from ui.backend.app.services import project_store
        from ui.backend.app.services.project_paths import get_db_path

        items = project_store.list_foreshadowing_legacy(
            get_db_path(), project_id,
        )
        # Filter to only the unresolved ones — the legacy method
        # returns all hooks, but for prompt context we only want
        # those still pending.
        active = [
            it for it in items
            if it.get("status") in
            ("open", "progressing", "pressured", "near_payoff")
        ]
        if not active:
            return ""
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
            return ""
        body = clip("\n".join(lines), BUDGETS["foreshadowing"])
        return section("关联伏笔（本章需埋设或回收的伏笔）", body)
    except Exception as e:
        logger.debug("foreshadowing skipped: %s", e)
        return ""
