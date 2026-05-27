"""Chapter outline loader (LOADER_SPEC Loader 7).

Renders the chapter's local fields — synopsis (the user's outline),
time/location, and the on-stage entities envelope (characters /
locations / items / organizations) populated in Batch 2.

The output is the Writer's core task statement: what to write, when
and where, with which entities on stage. Other loaders supply the
"how" (style, references, memory) but this one is the brief.
"""
from __future__ import annotations

import logging

from ..budgets import BUDGETS
from ..chapter_fields import load_chapter_fields
from ..utils import clip, section

logger = logging.getLogger("inkoctobot.services.prompt_context.chapter_outline")


def load(project_id: str, chapter_id: str = "", exclude: set | None = None) -> str:
    """Render the chapter's outline block.

    ``exclude`` is accepted for API symmetry but unused — the outline
    is a single atom; users de-select it via the ``chapter_outline``
    bucket on the builder side (which substitutes ``""`` for this block).
    """
    if not chapter_id:
        return ""
    try:
        fields = load_chapter_fields(project_id, chapter_id)
        synopsis = (fields.get("synopsis") or "").strip()
        time_setting = (fields.get("time_setting") or "").strip()
        location = (fields.get("location") or "").strip()
        entities = fields.get("on_stage_entities") or {}
        # If the chapter has no signal at all (unknown chapter_id or a
        # freshly-stubbed row), don't emit an empty placeholder block —
        # callers prefer the absence over "（暂无大纲）".
        has_any_entity = any(
            (entities.get(k) or [])
            for k in ("characters", "locations", "items", "organizations")
        )
        if not (synopsis or time_setting or location or has_any_entity):
            return ""

        parts: list[str] = []
        parts.append("### 主线")
        parts.append(synopsis or "（暂无大纲）")

        if time_setting or location:
            parts.append("\n### 时间地点")
            if time_setting:
                parts.append(f"时间：{time_setting}")
            if location:
                parts.append(f"地点：{location}")

        def _fmt(label: str, key: str) -> None:
            vals = entities.get(key) or []
            if isinstance(vals, list) and vals:
                parts.append(f"{label}：{', '.join(str(v) for v in vals)}")

        ent_lines: list[str] = []
        for label, key in [
            ("出场角色", "characters"),
            ("涉及地点", "locations"),
            ("涉及物品", "items"),
            ("涉及组织", "organizations"),
        ]:
            vals = entities.get(key) or []
            if isinstance(vals, list) and vals:
                ent_lines.append(f"{label}：{', '.join(str(v) for v in vals)}")
        if ent_lines:
            parts.append("")  # blank line before the entity lines
            parts.extend(ent_lines)

        body = clip("\n".join(parts), BUDGETS["chapter_outline"])
        return section("本章大纲", body)
    except Exception as e:
        logger.debug("chapter_outline skipped: %s", e)
        return ""
