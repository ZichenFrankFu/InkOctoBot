"""RAG context assembly — the public API of the prompt_context package.

Four entry points, all read-only and no-LLM:

- ``build_generation_context``  — every RAG block keyed by block name
- ``build_rag_digest``           — single concatenated grounding text
                                    (used by the evaluator)
- ``creation_context_manifest``  — RAG categories + items for the UI
                                    creation-tab de-selection panel
- ``single_agent_vars``          — full variable dict for the
                                    ``generation.single_agent`` template

All four share the same loader chain so the previewed, copied and
generated prompt are byte-identical.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from .chapter_fields import load_chapter_fields
from .loaders import (
    adjacent_context,
    character_cards,
    foreshadowing,
    platform_market,
    reference_blocks,
    style_calibration,
    user_preferences,
    worldbook,
    writing_knowledge,
)
from .references import build_referenced_materials_block
from .skills_block import (
    active_learned_skills,
    build_simple_block,
    creation_default_skills,
    load_writing_skills,
)
from .utils import parse_rag_excludes, section
from .loaders.writing_knowledge import project_writing_knowledge

logger = logging.getLogger("inkoctobot.services.prompt_context.builder")


def build_generation_context(
    project_id: str,
    chapter_num: int = 1,
    characters: list[str] | None = None,
    db_path: str | None = None,
    skills: list[str] | None = None,
    chapter_id: str = "",
    rag_excludes: list[str] | None = None,
) -> dict:
    """Assemble the RAG context for a chapter-generation call.

    Returns ``{"blocks": {...}, "sections": [{label, content}],
    "token_estimate": int}``. Every block value is either ``""`` or a
    self-contained ``\\n\\n## 标题\\n...`` string ready to splice into the
    ``generation.single_agent`` template. ``rag_excludes`` carries the
    user's per-item de-selections (``"block::id"``).
    """
    characters = characters or []
    if db_path is None:
        try:
            from ui.backend.app.services import get_db_path
            db_path = get_db_path()
        except Exception:
            db_path = ""

    excl = parse_rag_excludes(rag_excludes)
    blocks: dict[str, str] = {
        "platform_directive": platform_market.load(project_id, excl.get("platform")),
        "style_calibration":  style_calibration.load(project_id),
        "character_cards":    character_cards.load(project_id, characters, excl.get("character_cards")),
        "worldbook":          worldbook.load(project_id, excl.get("worldbook")),
        "reference_summary":  reference_blocks.load(project_id, db_path or "", excl.get("reference_summary")),
        "writing_knowledge":  writing_knowledge.load(project_id, excl.get("writing_knowledge")),
        "writing_skills":     load_writing_skills(skills),
        "adjacent_context":   adjacent_context.load(project_id, chapter_id, excl.get("adjacent_context")),
        "foreshadowing":      foreshadowing.load(project_id, chapter_id),
        "user_preferences":   user_preferences.load(project_id, db_path or ""),
    }
    if "__all__" in excl.get("foreshadowing", set()):
        blocks["foreshadowing"] = ""

    sections: list[dict[str, str]] = []
    for val in blocks.values():
        v = (val or "").strip()
        if not v:
            continue
        # block looks like "## 标题\n内容"
        head, _, rest = v.partition("\n")
        sections.append({"label": head.lstrip("# ").strip(), "content": rest.strip()})

    token_estimate = sum(len(v) for v in blocks.values()) // 2
    return {"blocks": blocks, "sections": sections, "token_estimate": token_estimate}


def build_rag_digest(
    project_id: str,
    chapter_num: int = 1,
    characters: list[str] | None = None,
    chapter_id: str = "",
    rag_excludes: list[str] | None = None,
) -> str:
    """Fold the project's RAG blocks into one grounding digest.

    Used to give the evaluator the same project context the generation
    step had — same loader chain, same exclusions.
    """
    try:
        ctx = build_generation_context(
            project_id, chapter_num, characters or [],
            chapter_id=chapter_id, rag_excludes=rag_excludes,
        )
        return "\n".join(
            b for b in (
                ctx["blocks"].get("platform_directive", ""),
                ctx["blocks"].get("character_cards", ""),
                ctx["blocks"].get("worldbook", ""),
                ctx["blocks"].get("adjacent_context", ""),
                ctx["blocks"].get("reference_summary", ""),
                ctx["blocks"].get("writing_knowledge", ""),
            ) if (b or "").strip()
        ).strip()
    except Exception as e:
        logger.debug("rag digest skipped: %s", e)
        return ""


def creation_context_manifest(
    project_id: str,
    chapter_id: str = "",
    chapter_num: int = 1,
    mode: str = "cluster",
) -> dict:
    """Summarize the skills + RAG a chapter generation will use.

    Returns the concrete items behind each RAG category so the creation
    tab can show them and let the user de-select individual items.
    """
    from ui.backend.app.routers.json_storage_api import _col, _safe_id, _list

    fields = load_chapter_fields(project_id, chapter_id)
    characters = fields.get("characters") or []
    ctx = build_generation_context(
        project_id, chapter_num, characters, chapter_id=chapter_id)
    blocks = ctx["blocks"]

    def _has(k: str) -> bool:
        return bool((blocks.get(k) or "").strip())

    platform_items: list[dict] = []
    try:
        pp = _col("projects") / f"{_safe_id(project_id)}.json"
        if pp.exists():
            plat = (json.loads(pp.read_text("utf-8")).get("platform") or "").strip()
            if plat:
                platform_items = [{"id": "platform", "label": plat}]
    except Exception:
        pass

    wb_items: list[dict] = []
    try:
        for e in _list("worldbook", filter_key="project_id", filter_value=project_id):
            t = (e.get("title") or "").strip()
            if t or (e.get("content") or "").strip():
                wb_items.append({"id": str(e.get("id") or t), "label": t or "（无题）"})
    except Exception:
        pass

    ref_items: list[dict] = []
    try:
        from knowledge.reference_db import ReferenceDB
        from ui.backend.app.services import get_db_path

        rdb = ReferenceDB(get_db_path())
        seen: set[str] = set()
        for link in (rdb.get_project_links(project_id) or []):
            rid = link.get("ref_id")
            if not rid or rid in seen:
                continue
            seen.add(rid)
            w = rdb.get_work(rid) or {}
            ref_items.append({"id": str(rid), "label": w.get("title") or str(rid)})
    except Exception:
        pass

    rm_items: list[dict] = []
    for e in (fields.get("referenced_events") or []):
        nm = str(e.get("name") or e.get("description") or "").strip()
        if nm:
            rm_items.append({"id": "event:" + str(e.get("id") or nm),
                             "label": "事件·" + nm[:20]})
    for ins in (fields.get("referenced_inspirations") or []):
        t = str(ins.get("title") or ins.get("content") or "").strip()
        if t:
            rm_items.append({"id": "insp:" + str(ins.get("id") or t),
                             "label": "灵感·" + t[:20]})

    has_outline = bool((fields.get("synopsis") or "").strip())
    rag = [
        {"key": "platform", "label": "发布平台", "items": platform_items,
         "present": bool(platform_items)},
        {"key": "character_cards", "label": "人物卡",
         "items": [{"id": n, "label": n} for n in characters],
         "present": _has("character_cards")},
        {"key": "worldbook", "label": "世界书", "items": wb_items,
         "present": _has("worldbook")},
        {"key": "chapter_outline", "label": "本章大纲",
         "items": [{"id": "__all__", "label": "本章大纲"}] if has_outline else [],
         "present": has_outline},
        {"key": "adjacent_context", "label": "前章结尾 / 后章大纲",
         "items": [{"id": "prev", "label": "前一章结尾"}, {"id": "next", "label": "后一章大纲"}]
         if _has("adjacent_context") else [],
         "present": _has("adjacent_context")},
        {"key": "reference_summary", "label": "关联参考作品", "items": ref_items,
         "present": _has("reference_summary")},
        {"key": "referenced_materials", "label": "关联灵感 / 事件", "items": rm_items,
         "present": bool(rm_items)},
        {"key": "foreshadowing", "label": "伏笔",
         "items": [{"id": "__all__", "label": "未回收伏笔"}] if _has("foreshadowing") else [],
         "present": _has("foreshadowing")},
    ]
    return {
        "rag": rag,
        "default_skills": creation_default_skills(mode),
        "learned_skills": [
            {"name": s["name"], "description": s["description"], "skill_md": s["body"]}
            for s in active_learned_skills()
        ],
        "writing_knowledge": project_writing_knowledge(project_id),
    }


def single_agent_vars(
    project_id: str,
    chapter_num: int = 1,
    synopsis: str = "",
    time_setting: str = "",
    location: str = "",
    characters: list[str] | None = None,
    existing_content: str = "",
    character_aliases: dict[str, str] | None = None,
    skills: list[str] | None = None,
    referenced_events: list[dict] | None = None,
    referenced_inspirations: list[dict] | None = None,
    db_path: str | None = None,
    chapter_id: str = "",
    rag_excludes: list[str] | None = None,
    web_mode: bool = False,
) -> dict:
    """Assemble the full variable dict for the ``generation.single_agent`` template.

    RAG blocks plus chapter-local blocks. Shared by ``/quick-generate``
    and the prompt preview endpoint so the previewed, copied and
    generated prompt are identical.
    """
    characters = characters or []
    if db_path is None:
        try:
            from ui.backend.app.services import get_db_path
            db_path = get_db_path()
        except Exception:
            db_path = ""
    ctx = build_generation_context(
        project_id, chapter_num, characters, db_path=db_path, skills=skills,
        chapter_id=chapter_id, rag_excludes=rag_excludes)
    blocks: dict[str, str] = dict(ctx["blocks"])
    excl = parse_rag_excludes(rag_excludes)

    synopsis = (synopsis or "").strip()
    if "__all__" in excl.get("chapter_outline", set()):
        blocks["chapter_outline"] = ""
    else:
        blocks["chapter_outline"] = section(
            "章节大纲",
            synopsis or "（未提供章节大纲，请根据已有正文与设定合理推进剧情）",
        )

    tl: list[str] = []
    if time_setting:
        tl.append(f"时间：{time_setting}")
    if location:
        tl.append(f"地点：{location}")
    blocks["time_location"] = section("时间与地点", "\n".join(tl))

    aliases = character_aliases or {}
    if characters:
        display = "、".join(aliases.get(c, c) for c in characters)
        blocks["characters_block"] = section(
            "本章出场角色",
            f"{display}\n仅允许上述角色出场，禁止引入未列出的角色。",
        )
    else:
        blocks["characters_block"] = ""

    existing = (existing_content or "").strip()
    blocks["existing_content"] = (
        section("已有正文（需在此基础上续写，保持风格一致）", existing[-800:])
        if len(existing) > 10
        else ""
    )

    # Active learned skills (from build_generation_context) take priority;
    # fall back to an explicit name list when no learned skills are active.
    # web_mode rebuilds the block as a Skill-Access check for a copy-to-web prompt.
    blocks["skills_block"] = blocks.pop("writing_skills", "") or build_simple_block(skills)
    if web_mode:
        blocks["skills_block"] = load_writing_skills(skills, web_mode=True)
    blocks["referenced_materials"] = build_referenced_materials_block(
        referenced_events, referenced_inspirations, db_path or "",
    )
    return blocks
