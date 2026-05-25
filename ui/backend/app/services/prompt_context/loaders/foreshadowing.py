"""Legacy foreshadowing loader (reads ``data/foreshadowing/<pid>.json``).

Note: this is the OLD storage surface. Once Truth-File integration
finishes (phase 4 of the architecture review), this will be replaced
by the ``truth_files.pending_hooks`` loader.
"""
from __future__ import annotations

import json
import logging

from ..budgets import BUDGETS
from ..utils import clip, section

logger = logging.getLogger("inkoctobot.services.prompt_context.foreshadowing")


def load(project_id: str, chapter_id: str = "") -> str:
    """Inject the user-managed 伏笔 (from the 大纲 tab) linked to this chapter."""
    if not chapter_id:
        return ""
    try:
        from ui.backend.app.routers.json_storage_api import _col, _safe_id

        p = _col("foreshadowing") / f"{_safe_id(project_id)}.json"
        if not p.exists():
            return ""
        items = json.loads(p.read_text("utf-8")).get("items", [])
        lines: list[str] = []
        for f in items:
            if chapter_id not in (f.get("chapter_ids") or []):
                continue
            title = str(f.get("title") or "").strip()
            content = str(f.get("content") or "").strip()
            if not title and not content:
                continue
            lines.append(f"- 【{title or '伏笔'}】{content}".rstrip())
        if not lines:
            return ""
        body = clip("\n".join(lines), BUDGETS["foreshadowing"])
        return section("关联伏笔（本章需埋设或回收的伏笔）", body)
    except Exception as e:
        logger.debug("foreshadowing skipped: %s", e)
        return ""
