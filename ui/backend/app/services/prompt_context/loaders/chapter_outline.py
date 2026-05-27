"""Chapter outline loader (LOADER_SPEC Loader 7).

Renders the chapter's local fields — synopsis (the user's outline),
time/location, and the on-stage entities envelope (characters /
locations / items / organizations) populated in Batch 2.
"""
from __future__ import annotations

import logging

from ..budget_allocator import LOADER_BUDGETS
from ..chapter_fields import load_chapter_fields
from ..loader_protocol import LoaderPlan
from ..utils import clip, section

logger = logging.getLogger("inkoctobot.services.prompt_context.chapter_outline")


_BLOCK = "chapter_outline"
_TITLE = "本章大纲"


def _build_body(fields: dict) -> str:
    synopsis = (fields.get("synopsis") or "").strip()
    time_setting = (fields.get("time_setting") or "").strip()
    location = (fields.get("location") or "").strip()
    entities = fields.get("on_stage_entities") or {}

    parts: list[str] = []
    parts.append("### 主线")
    parts.append(synopsis or "（暂无大纲）")

    if time_setting or location:
        parts.append("\n### 时间地点")
        if time_setting:
            parts.append(f"时间：{time_setting}")
        if location:
            parts.append(f"地点：{location}")

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
        parts.append("")
        parts.extend(ent_lines)

    return "\n".join(parts)


def plan(
    project_id: str, chapter_id: str = "",
    exclude: set | None = None,
) -> LoaderPlan | None:
    if not chapter_id:
        return None
    try:
        fields = load_chapter_fields(project_id, chapter_id)
    except Exception as e:
        logger.debug("chapter_outline lookup failed: %s", e)
        return None

    entities = fields.get("on_stage_entities") or {}
    has_any_entity = any(
        (entities.get(k) or [])
        for k in ("characters", "locations", "items", "organizations")
    )
    synopsis = (fields.get("synopsis") or "").strip()
    time_setting = (fields.get("time_setting") or "").strip()
    location = (fields.get("location") or "").strip()
    if not (synopsis or time_setting or location or has_any_entity):
        return None

    body = _build_body(fields)
    cfg = LOADER_BUDGETS[_BLOCK]
    natural = len(_TITLE) + 6 + len(body)

    def render(budget: int) -> str:
        return section(_TITLE, clip(body, max(0, budget - len(_TITLE) - 6)))

    return LoaderPlan(
        block_id=_BLOCK,
        natural_length=natural,
        minimum=cfg["min"], target=cfg["target"], maximum=cfg["max"],
        priority_tier=cfg["tier"], render=render,
    )


def load(project_id: str, chapter_id: str = "", exclude: set | None = None) -> str:
    p = plan(project_id, chapter_id, exclude)
    return p.render(p.target) if p else ""
