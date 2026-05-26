"""Reader for a chapter's local fields from the editor doc (DB-backed)."""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("inkoctobot.services.prompt_context.chapter_fields")


def load_chapter_fields(project_id: str, chapter_id: str) -> dict[str, Any]:
    """Read a chapter's local fields (outline / time / location / characters /
    existing text) from the editor doc.

    Returns the populated fields, or an empty dict with all keys present
    when the chapter is missing.
    """
    fields: dict[str, Any] = {
        "synopsis": "", "time_setting": "", "location": "",
        "characters": [], "existing_content": "",
        "referenced_events": [], "referenced_inspirations": [],
    }
    if not chapter_id:
        return fields
    try:
        from ui.backend.app.services import project_store
        from ui.backend.app.services.project_paths import get_db_path

        data = project_store.get_editor_doc(get_db_path(), project_id)
        for vol in data.get("volumes", []) or []:
            for ch in vol.get("chapters", []) or []:
                if ch.get("id") == chapter_id:
                    fields["synopsis"] = ch.get("synopsis", "") or ""
                    fields["time_setting"] = ch.get("time", "") or ""
                    fields["location"] = ch.get("location", "") or ""
                    fields["characters"] = ch.get("characters", []) or []
                    fields["existing_content"] = ch.get("content", "") or ""
                    fields["referenced_events"] = ch.get("referenced_events", []) or []
                    fields["referenced_inspirations"] = ch.get("referenced_inspirations", []) or []
                    return fields
    except Exception as e:
        logger.debug("load chapter fields skipped: %s", e)
    return fields
