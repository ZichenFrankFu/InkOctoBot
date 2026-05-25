"""Adjacent-chapter context loader.

Pulls the previous chapter's ending excerpt (last 800 chars) and the
next chapter's outline so the new chapter flows naturally from what
came before and sets up what follows.
"""
from __future__ import annotations

import json
import logging

from ..budgets import BUDGETS
from ..utils import clip, section

logger = logging.getLogger("inkoctobot.services.prompt_context.adjacent_context")


def load(project_id: str, chapter_id: str, exclude: set | None = None) -> str:
    """Inject the previous chapter's ending + the next chapter's outline."""
    if not chapter_id:
        return ""
    exclude = exclude or set()
    try:
        from ui.backend.app.routers.json_storage_api import _col, _safe_id

        p = _col("editor") / f"{_safe_id(project_id)}.json"
        if not p.exists():
            return ""
        data = json.loads(p.read_text("utf-8"))
        chapters: list[dict] = []
        for vol in data.get("volumes", []):
            for ch in vol.get("chapters", []):
                chapters.append(ch)
        idx = next(
            (i for i, ch in enumerate(chapters) if ch.get("id") == chapter_id), -1,
        )
        if idx < 0:
            return ""
        parts: list[str] = []
        if idx > 0 and "prev" not in exclude:
            prev = chapters[idx - 1]
            prev_text = (prev.get("content") or "").strip()
            if prev_text:
                tail = prev_text[-800:]
                if len(prev_text) > 800:
                    tail = "……" + tail
                parts.append(
                    f"【前一章结尾】（{prev.get('title') or '上一章'}）\n{tail}"
                )
        if idx + 1 < len(chapters) and "next" not in exclude:
            nxt = chapters[idx + 1]
            nxt_outline = (nxt.get("synopsis") or "").strip()
            if nxt_outline:
                parts.append(
                    f"【后一章大纲】（{nxt.get('title') or '下一章'}）\n{nxt_outline}"
                )
        if not parts:
            return ""
        body = clip("\n\n".join(parts), BUDGETS["adjacent_context"])
        return section(
            "上下文衔接（须与前一章结尾自然承接，并为后一章大纲预留铺垫）", body,
        )
    except Exception as e:
        logger.debug("adjacent context skipped: %s", e)
        return ""
