"""Project-memory loader.

The user-confirmed facts / decisions that persist across every AI
conversation in the project. Stored in ``data/project_memory/<pid>.json``.
"""
from __future__ import annotations

import json
import logging

from ..budgets import BUDGETS
from ..utils import clip, section

logger = logging.getLogger("inkoctobot.services.prompt_context.project_memory")


def load(project_id: str, exclude: set | None = None) -> str:
    """Inject the project's shared memory."""
    try:
        from ui.backend.app.routers.json_storage_api import _col, _safe_id

        p = _col("project_memory") / f"{_safe_id(project_id)}.json"
        if not p.exists():
            return ""
        data = json.loads(p.read_text("utf-8"))
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
