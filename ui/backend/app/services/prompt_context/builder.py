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

v3.1 cleanup: ``reference_summary`` / ``writing_knowledge`` /
``writing_skills`` / ``adjacent_context`` loaders are gone. Dynamic
budget allocation lives in ``budget_allocator``; per-loader telemetry
is returned in ``diagnostics``.
"""
from __future__ import annotations

import logging
import time
from typing import Any

from .budget_allocator import LOADER_BUDGETS, allocate, TOTAL_TARGET
from .budgets import BUDGETS  # back-compat alias preserved
from .chapter_fields import load_chapter_fields
from .loader_protocol import LoaderPlan
from .loaders import (
    chapter_outline as chapter_outline_loader,
    character_cards,
    current_chapter_draft,
    foreshadowing,
    inspiration,
    platform_market,
    reader_memory as reader_memory_loader,
    reference as reference_loader,
    skills as skills_loader,
    storyland_state as storyland_state_loader,
    subplots as subplots_loader,
    user_preferences,
    user_special_requirements,
    worldbook,
)
from .references import build_referenced_materials_block
from .token_counter import get_token_counter
from .utils import parse_rag_excludes, section

logger = logging.getLogger("inkoctobot.services.prompt_context.builder")


# Diagnostics alert thresholds. Tweak here so the next reader doesn't
# go hunting through the diagnostics-assembly code.
EMPTY_THRESHOLD = 0.1       # utilization below this lands in alerts.empty
PRESSURE_THRESHOLD = 0.9    # utilization above this lands in alerts.pressure
DOMINANT_THRESHOLD = 0.3    # share_of_total_tokens above this → alerts.dominant


# Logical section grouping for diagnostics.sections. The full prompt's
# system/user split happens at the Jinja template layer (which the
# builder doesn't see); these groupings classify each context block by
# what role it plays in the final prompt so the diagnostics caller has
# a coarse 3-bucket view without re-implementing prompt assembly.
_SECTION_GROUPS: dict[str, str] = {
    # system — directives & cross-chapter writing preferences
    "platform_directive":         "system",
    "user_preferences":           "system",
    # context — background grounding (RAG-style)
    "worldbook":                  "context",
    "reference":                  "context",
    "character_cards":            "context",
    "foreshadowing":              "context",
    "inspiration":                "context",
    "subplots":                   "context",
    "skills":                     "context",
    "storyland_state":            "context",
    "reader_memory":              "context",
    # user — this chapter's specific inputs
    "chapter_outline":            "user",
    "current_chapter_draft":      "user",
    "user_special_requirements":  "user",
}


# Ordered list of (block_id, plan_callable). Plan callables take
# ``(ctx_state)`` and return a ``LoaderPlan | None``. Builder runs each,
# allocates, then renders.
def _plan_callables(
    *, project_id: str, chapter_num: int, characters: list[str],
    db_path: str, chapter_id: str, chapter_fields: dict,
    chapter_synopsis: str, on_stage_characters: list[str],
    on_stage_entities: dict, excl: dict, generation_mode: str,
    revision_anchor: dict | None, linked_inspiration_ids: list[str] | None,
) -> list[tuple[str, Any]]:
    """Return [(block_id, plan_callable)] in declaration order."""
    return [
        ("platform_directive", lambda: platform_market.plan(
            project_id, exclude=excl.get("platform"),
        )),
        ("user_preferences", lambda: user_preferences.plan(
            project_id, db_path or "", exclude=excl.get("user_preferences"),
        )),
        ("user_special_requirements", lambda: user_special_requirements.plan(
            project_id, chapter_id,
            exclude=excl.get("user_special_requirements"),
        )),
        ("chapter_outline", lambda: chapter_outline_loader.plan(
            project_id, chapter_id, exclude=excl.get("chapter_outline"),
        )),
        ("character_cards", lambda: character_cards.plan(
            project_id, characters, exclude=excl.get("character_cards"),
            chapter_num=chapter_num,
        )),
        ("worldbook", lambda: worldbook.plan(
            project_id, chapter_id, exclude=excl.get("worldbook"),
        )),
        ("reference", lambda: reference_loader.plan(
            project_id, db_path or "", chapter_synopsis,
            exclude=excl.get("reference"),
        )),
        ("foreshadowing", lambda: foreshadowing.plan(
            project_id, chapter_id, exclude=excl.get("foreshadowing"),
        )),
        ("inspiration", lambda: inspiration.plan(
            project_id, chapter_synopsis, on_stage_characters,
            user_pinned_ids=linked_inspiration_ids,
            chapter_num=chapter_num,
            exclude=excl.get("inspiration"),
        )),
        ("current_chapter_draft", lambda: current_chapter_draft.plan(
            project_id, chapter_id,
            generation_mode=generation_mode,
            revision_anchor=revision_anchor,
            exclude=excl.get("current_chapter_draft"),
        )),
        ("subplots", lambda: subplots_loader.plan(
            project_id, chapter_synopsis, chapter_num,
            exclude=excl.get("subplots"),
        )),
        ("skills", lambda: skills_loader.plan(
            project_id, chapter_synopsis, on_stage_characters,
            exclude=excl.get("skills"),
        )),
        ("storyland_state", lambda: storyland_state_loader.plan(
            project_id, chapter_num,
            on_stage_entities=on_stage_entities,
            exclude=excl.get("storyland_state"),
        )),
        ("reader_memory", lambda: reader_memory_loader.plan(
            project_id, chapter_num,
            chapter_outline=chapter_synopsis,
            on_stage_characters=on_stage_characters,
            exclude=excl.get("reader_memory"),
        )),
    ]


def build_generation_context(
    project_id: str,
    chapter_num: int = 1,
    characters: list[str] | None = None,
    db_path: str | None = None,
    skills: list[str] | None = None,
    chapter_id: str = "",
    rag_excludes: list[str] | None = None,
    *,
    generation_mode: str = "fresh",
    revision_anchor: dict | None = None,
    linked_inspiration_ids: list[str] | None = None,
    total_budget: int | None = None,
) -> dict:
    """Assemble the RAG context for a chapter-generation call.

    Returns ``{"blocks": {...}, "sections": [...], "token_estimate": int,
    "diagnostics": {...}}``. Every block value is either ``""`` or a
    self-contained ``\\n\\n## 标题\\n...`` string ready to splice into
    the ``generation.single_agent`` template. ``rag_excludes`` carries
    the user's per-item de-selections (``"block::id"``).

    Three-stage pipeline:
      1. **Plan**: each loader does its DB / embedding queries, returns
         ``(natural_length, render_callable)`` or ``None`` (inactive).
      2. **Allocate**: the dynamic budget allocator hands each plan an
         actual character budget based on tier + natural lengths.
      3. **Render**: each plan's render callable produces the final
         block string at its allocated budget.

    Returned ``diagnostics.loaders[<block_id>]`` carries per-block
    chars / tokens / natural_length / allocated_budget / tier /
    plan_ms / render_ms / status.
    """
    characters = characters or []
    if db_path is None:
        try:
            from ui.backend.app.services import get_db_path
            db_path = get_db_path()
        except Exception:
            db_path = ""

    excl = parse_rag_excludes(rag_excludes)

    chapter_fields = load_chapter_fields(project_id, chapter_id) if chapter_id else {}
    chapter_synopsis = (chapter_fields.get("synopsis") or "").strip()
    on_stage_characters = (
        (chapter_fields.get("on_stage_entities") or {}).get("characters")
        or chapter_fields.get("characters") or characters
    )
    on_stage_entities = chapter_fields.get("on_stage_entities") or {}

    # ── Phase 1: plan ───────────────────────────────────────────
    callables = _plan_callables(
        project_id=project_id, chapter_num=chapter_num,
        characters=characters, db_path=db_path or "",
        chapter_id=chapter_id, chapter_fields=chapter_fields,
        chapter_synopsis=chapter_synopsis,
        on_stage_characters=list(on_stage_characters),
        on_stage_entities=on_stage_entities,
        excl=excl, generation_mode=generation_mode,
        revision_anchor=revision_anchor,
        linked_inspiration_ids=linked_inspiration_ids,
    )

    plans: list[LoaderPlan] = []
    plan_ms: dict[str, float] = {}
    inactive: list[str] = []
    for block_id, fn in callables:
        # Honour ``<block_id>::__all__`` exclude as an early kill switch.
        if "__all__" in (excl.get(block_id) or set()):
            inactive.append(block_id)
            plan_ms[block_id] = 0.0
            continue
        t0 = time.perf_counter()
        try:
            p = fn()
        except Exception as e:
            logger.debug("loader %s plan failed: %s", block_id, e)
            p = None
        plan_ms[block_id] = (time.perf_counter() - t0) * 1000.0
        if p is None:
            inactive.append(block_id)
        else:
            plans.append(p)

    # ── Phase 2: allocate ───────────────────────────────────────
    allocations = allocate(plans, total_budget=total_budget or TOTAL_TARGET)

    # ── Phase 3: render ─────────────────────────────────────────
    blocks: dict[str, str] = {bid: "" for bid, _ in callables}
    render_ms: dict[str, float] = {bid: 0.0 for bid, _ in callables}
    for p in plans:
        budget = allocations.get(p.block_id, p.target)
        t0 = time.perf_counter()
        try:
            rendered = p.render(budget) or ""
        except Exception as e:
            logger.debug("loader %s render failed: %s", p.block_id, e)
            rendered = ""
        render_ms[p.block_id] = (time.perf_counter() - t0) * 1000.0
        blocks[p.block_id] = rendered

    # ── Sections list (back-compat preserve) ────────────────────
    sections: list[dict[str, str]] = []
    for v in blocks.values():
        v = (v or "").strip()
        if not v:
            continue
        head, _, rest = v.partition("\n")
        sections.append({"label": head.lstrip("# ").strip(), "content": rest.strip()})

    # ── Diagnostics ─────────────────────────────────────────────
    counter = get_token_counter()
    total_chars = sum(len(v) for v in blocks.values())
    # Cache per-block token counts — re-used for total, per-loader,
    # per-section, and share calculations below.
    block_tokens: dict[str, int] = {bid: counter.count(v) for bid, v in blocks.items()}
    total_tokens = sum(block_tokens.values())

    plan_by_id = {p.block_id: p for p in plans}
    diag_loaders: dict[str, dict[str, Any]] = {}
    for block_id, _ in callables:
        body = blocks[block_id]
        chars = len(body)
        tokens = block_tokens[block_id]
        p = plan_by_id.get(block_id)
        if p is None:
            status = "inactive"
            natural = 0
            allocated = 0
            tier = LOADER_BUDGETS.get(block_id, {}).get("tier", 0)
        else:
            natural = p.natural_length
            allocated = allocations.get(block_id, p.target)
            tier = p.priority_tier
            status = "clipped" if natural > allocated else "rendered"
        utilization = (chars / allocated) if allocated > 0 else 0.0
        share = (tokens / total_tokens) if total_tokens > 0 else 0.0
        diag_loaders[block_id] = {
            "status":                 status,
            "chars":                  chars,
            "tokens":                 tokens,
            "natural_length":         natural,
            "allocated_budget":       allocated,
            "utilization":            round(utilization, 4),
            "share_of_total_tokens":  round(share, 4),
            "tier":                   tier,
            "plan_ms":                round(plan_ms.get(block_id, 0.0), 3),
            "render_ms":              round(render_ms.get(block_id, 0.0), 3),
        }

    # Per-section roll-up.
    section_bins: dict[str, dict[str, int]] = {
        "system":  {"chars": 0, "tokens": 0},
        "context": {"chars": 0, "tokens": 0},
        "user":    {"chars": 0, "tokens": 0},
    }
    for block_id, v in blocks.items():
        group = _SECTION_GROUPS.get(block_id)
        if group is None or not v:
            continue
        section_bins[group]["chars"] += len(v)
        section_bins[group]["tokens"] += block_tokens[block_id]

    # Alerts.
    alerts: dict[str, list[str]] = {"empty": [], "pressure": [], "dominant": []}
    for block_id, info in diag_loaders.items():
        if info["status"] == "inactive" or info["utilization"] < EMPTY_THRESHOLD:
            alerts["empty"].append(block_id)
        if info["status"] == "clipped" or info["utilization"] > PRESSURE_THRESHOLD:
            alerts["pressure"].append(block_id)
        if info["share_of_total_tokens"] > DOMINANT_THRESHOLD:
            alerts["dominant"].append(block_id)

    return {
        "blocks":         blocks,
        "sections":       sections,
        "token_estimate": total_tokens,
        "diagnostics": {
            "total": {
                "chars":            total_chars,
                "tokens":           total_tokens,
                "tokenizer_method": counter.get_method(),
            },
            "sections": section_bins,
            "loaders":  diag_loaders,
            "alerts":   alerts,
        },
    }


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
                ctx["blocks"].get("storyland_state", ""),
                ctx["blocks"].get("reference", ""),
                ctx["blocks"].get("reader_memory", ""),
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
    from ui.backend.app.services import project_store
    from ui.backend.app.services.project_paths import get_db_path
    from .skills_block import active_learned_skills, creation_default_skills

    fields = load_chapter_fields(project_id, chapter_id)
    characters = fields.get("characters") or []
    ctx = build_generation_context(
        project_id, chapter_num, characters, chapter_id=chapter_id)
    blocks = ctx["blocks"]

    def _has(k: str) -> bool:
        return bool((blocks.get(k) or "").strip())

    platform_items: list[dict] = []
    try:
        proj = project_store.get_project(get_db_path(), project_id) or {}
        plat = (proj.get("platform") or "").strip()
        if plat:
            platform_items = [{"id": "platform", "label": plat}]
    except Exception:
        pass

    wb_items: list[dict] = []
    try:
        for e in project_store.list_worldbook(get_db_path(), project_id):
            t = (e.get("title") or "").strip()
            if t or (e.get("content") or "").strip():
                wb_items.append({"id": str(e.get("id") or t), "label": t or "（无题）"})
    except Exception:
        pass

    ref_items: list[dict] = []
    try:
        from knowledge.reference_db import ReferenceDB
        from ui.backend.app.services import get_db_path as _gdb

        rdb = ReferenceDB(_gdb())
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
        {"key": "reference", "label": "关联参考作品", "items": ref_items,
         "present": _has("reference")},
        {"key": "referenced_materials", "label": "关联灵感 / 事件", "items": rm_items,
         "present": bool(rm_items)},
        {"key": "foreshadowing", "label": "伏笔",
         "items": [{"id": "__all__", "label": "未回收伏笔"}] if _has("foreshadowing") else [],
         "present": _has("foreshadowing")},
        {"key": "storyland_state", "label": "Storyland 客观状态",
         "items": [{"id": "__all__", "label": "全部"}] if _has("storyland_state") else [],
         "present": _has("storyland_state")},
        {"key": "reader_memory", "label": "读者视角记忆",
         "items": [{"id": "__all__", "label": "全部"}] if _has("reader_memory") else [],
         "present": _has("reader_memory")},
        {"key": "user_special_requirements", "label": "用户特别要求",
         "items": [{"id": "__all__", "label": "本章特别要求"}]
                  if _has("user_special_requirements") else [],
         "present": _has("user_special_requirements")},
    ]
    return {
        "rag": rag,
        "default_skills": creation_default_skills(mode),
        "learned_skills": [
            {"name": s["name"], "description": s["description"], "skill_md": s["body"]}
            for s in active_learned_skills()
        ],
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
    elif not blocks.get("chapter_outline"):
        # If the chapter_outline loader returned empty (no chapter_id
        # passed), fall back to the caller-supplied synopsis.
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

    # ``skills_block`` is no longer surfaced by the prompt-context loader
    # chain in v3.1; the new ``skills`` loader (Loader 14) carries the
    # SKILL.md content directly into ``blocks["skills"]``. Keep an empty
    # placeholder so the Jinja2 template can still reference the var
    # without breaking older builds.
    blocks["skills_block"] = ""
    blocks["referenced_materials"] = build_referenced_materials_block(
        referenced_events, referenced_inspirations, db_path or "",
    )
    return blocks
