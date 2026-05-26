"""Writing-knowledge entries loader.

Reads the per-project knowledge_injection selection and pulls those
writing_knowledge entries (curated craft notes the user wants the AI
to use) into a labeled prompt block.
"""
from __future__ import annotations

import logging

from ..budgets import BUDGETS
from ..utils import clip, section

logger = logging.getLogger("inkoctobot.services.prompt_context.writing_knowledge")


def _selected_ids_and_rows(project_id: str) -> tuple[set, list[dict]]:
    from ui.backend.app.services import project_store
    from ui.backend.app.services.project_paths import get_db_path

    db = get_db_path()
    sel = project_store.get_blob(db, project_id, "knowledge_injection")
    ids = sel.get("knowledge_ids")
    if not isinstance(ids, list) or not ids:
        return set(), []
    return set(ids), project_store.list_writing_knowledge(db)


def load(project_id: str, exclude: set | None = None) -> str:
    """Inject the writing-knowledge entries selected for this project."""
    try:
        wanted, rows = _selected_ids_and_rows(project_id)
        if not wanted:
            return ""
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
        wanted, rows = _selected_ids_and_rows(project_id)
        if not wanted:
            return []
        return [
            {"id": k.get("id"), "title": (k.get("title") or "").strip()}
            for k in rows
            if k.get("id") in wanted
        ]
    except Exception as e:
        logger.debug("project writing knowledge skipped: %s", e)
        return []
