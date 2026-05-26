"""Project-memory loader.

The user-confirmed facts / decisions that persist across every AI
conversation in the project. Reads from the v2-schema
``project_memories`` table (see docs/SCHEMA_REDESIGN.md).
"""
from __future__ import annotations

import logging

from ..budgets import BUDGETS
from ..utils import clip, section

logger = logging.getLogger("inkoctobot.services.prompt_context.project_memory")


def load(project_id: str, exclude: set | None = None) -> str:
    """Inject the project's shared memory."""
    try:
        from ui.backend.app.services import project_store
        from ui.backend.app.services.project_paths import get_db_path

        data = project_store.get_project_memory(get_db_path(), project_id)
        lines = [
            f"- {str(m.get('content') or '').strip()}"
            for m in data.get("memories", [])
            if str(m.get("content") or "").strip()
            and not (exclude and str(m.get("id")) in exclude)
        ]
        if not lines:
            return ""
        body = clip("\n".join(lines), BUDGETS["project_memory"])
        return section(
            "项目记忆（贯穿本项目所有 AI 对话的已确认信息，须与之保持一致）", body,
        )
    except Exception as e:
        logger.debug("project memory skipped: %s", e)
        return ""


def public_block(project_id: str) -> str:
    """Public accessor for the project-memory block (used by chat endpoints)."""
    return load(project_id)
