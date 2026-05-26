"""Reference-work × feature-type material loader.

Reads the per-project selection map (which reference work × which
feature types — characters / settings / plot / rhythm) and pulls the
matching condensed slices from the reference DB.
"""
from __future__ import annotations

import json
import logging

from ..budgets import BUDGETS
from ..references import (
    condense_ref_characters,
    condense_ref_plot,
    condense_ref_rhythm,
    condense_ref_settings,
)
from ..utils import clip, section

logger = logging.getLogger("inkoctobot.services.prompt_context.reference_blocks")


def _selection(project_id: str) -> dict:
    """Load the per-project reference-work × feature-type selection map."""
    try:
        from ui.backend.app.services import project_store
        from ui.backend.app.services.project_paths import get_db_path

        data = project_store.get_blob(
            get_db_path(), project_id, "reference_injection"
        )
        sel = data.get("selections")
        return sel if isinstance(sel, dict) else {}
    except Exception:
        return {}


def load(project_id: str, db_path: str, exclude: set | None = None) -> str:
    """Inject the user-selected reference-work × feature-type material."""
    selection = _selection(project_id)
    if not selection:
        return ""
    try:
        from knowledge.reference_db import ReferenceDB

        ref_db = ReferenceDB(db_path)
        links = ref_db.get_project_links(project_id)
        if not links:
            return ""
        seen: set[str] = set()
        blocks: list[str] = []
        for link in links:
            ref_id = link.get("ref_id", "")
            if not ref_id or ref_id in seen:
                continue
            if exclude and ref_id in exclude:
                continue
            seen.add(ref_id)
            feats = selection.get(ref_id)
            if not isinstance(feats, dict) or not any(feats.values()):
                continue
            work = ref_db.get_work(ref_id)
            if not work:
                continue
            title = work.get("title", "")
            condensed: list[str] = []
            if feats.get("characters"):
                condensed.append(condense_ref_characters(work.get("extracted_characters_json")))
            if feats.get("settings"):
                condensed.append(condense_ref_settings(work.get("settings_json")))
            if feats.get("plot"):
                condensed.append(condense_ref_plot(work.get("plot_outline_json")))
            if feats.get("rhythm"):
                condensed.append(
                    condense_ref_rhythm(
                        work.get("rhythm_json"), work.get("style_fingerprint_json")
                    )
                )
            condensed = [c for c in condensed if c]
            if condensed:
                blocks.append(f"《{title}》\n" + "\n".join(condensed))
        if not blocks:
            return ""
        body = clip("\n\n".join(blocks), BUDGETS["reference_summary"])
        return section(
            "参考作品借鉴（仅作创作借鉴，不可照抄）", body
        )
    except Exception as e:
        logger.debug("reference blocks skipped: %s", e)
        return ""
