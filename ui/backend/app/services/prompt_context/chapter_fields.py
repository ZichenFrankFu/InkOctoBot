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
        "referenced_settings": [],
        # LOADER_SPEC Loader 7: envelope of all on-stage entities the
        # chapter touches. Always returned; defaults to the legacy
        # ``characters`` list inside an otherwise-empty shape so
        # downstream loaders can rely on the keys being present.
        "on_stage_entities": {
            "characters": [], "locations": [], "items": [], "organizations": [],
        },
        # LOADER_SPEC user_special_requirements: per-chapter free-text
        # the Writer must honour for this chapter only. Empty by
        # default; loader skips itself when blank.
        "special_requirements": "",
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
                    fields["referenced_settings"] = ch.get("referenced_settings", []) or []
                    fields["special_requirements"] = (ch.get("special_requirements") or "").strip()
                    ose = ch.get("on_stage_entities")
                    if isinstance(ose, dict):
                        fields["on_stage_entities"] = {
                            "characters":    ose.get("characters") if isinstance(ose.get("characters"), list) else fields["characters"],
                            "locations":     ose.get("locations") if isinstance(ose.get("locations"), list) else [],
                            "items":         ose.get("items") if isinstance(ose.get("items"), list) else [],
                            "organizations": ose.get("organizations") if isinstance(ose.get("organizations"), list) else [],
                        }
                    else:
                        # No explicit blob in the editor doc — derive
                        # from the legacy ``characters`` list.
                        fields["on_stage_entities"] = {
                            "characters":    fields["characters"],
                            "locations":     [],
                            "items":         [],
                            "organizations": [],
                        }
                    return fields
    except Exception as e:
        logger.debug("load chapter fields skipped: %s", e)
    return fields
