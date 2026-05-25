"""Writing-knowledge entries loader.

Reads the per-project knowledge_injection selection and pulls those
writing_knowledge entries (curated craft notes the user wants the AI
to use) into a labeled prompt block.
"""
from __future__ import annotations

import json
import logging

from ..budgets import BUDGETS
from ..utils import clip, section

logger = logging.getLogger("inkoctobot.services.prompt_context.writing_knowledge")


def load(project_id: str, exclude: set | None = None) -> str:
    """Inject the writing-knowledge entries selected for this project."""
    try:
        from ui.backend.app.routers.json_storage_api import _col, _safe_id, _list

        p = _col("knowledge_injection") / f"{_safe_id(project_id)}.json"
        if not p.exists():
            return ""
        sel = json.loads(p.read_text("utf-8"))
        ids = sel.get("knowledge_ids")
        if not isinstance(ids, list) or not ids:
            return ""
        wanted = set(ids)
        rows = _list("writing_knowledge")
        out: list[str] = []
        for k in rows:
            if k.get("id") not in wanted:
                continue
            if exclude and str(k.get("id")) in exclude:
                continue
            title = (k.get("title") or "").strip()
            content = (k.get("content") or "").strip()
            if not content:
                continue
            domain = (k.get("domain") or "").strip()
            head = f"【{title}】" + (f"（{domain}）" if domain else "")
            out.append(f"{head} {content}".strip())
        if not out:
            return ""
        body = clip("\n".join(out), BUDGETS["writing_knowledge"])
        return section(
            "专业写作知识（世界观与设定须与之严谨一致）", body
        )
    except Exception as e:
        logger.debug("writing knowledge skipped: %s", e)
        return ""


def project_writing_knowledge(project_id: str) -> list[dict]:
    """Writing-knowledge entries injected for the project.

    Returns ``[{id, title}]`` (no content) — used by the creation manifest
    so the UI can show what's wired up.
    """
    try:
        from ui.backend.app.routers.json_storage_api import _col, _safe_id, _list

        p = _col("knowledge_injection") / f"{_safe_id(project_id)}.json"
        if not p.exists():
            return []
        ids = set(json.loads(p.read_text("utf-8")).get("knowledge_ids") or [])
        if not ids:
            return []
        return [
            {"id": k.get("id"), "title": (k.get("title") or "").strip()}
            for k in _list("writing_knowledge")
            if k.get("id") in ids
        ]
    except Exception as e:
        logger.debug("project writing knowledge skipped: %s", e)
        return []
