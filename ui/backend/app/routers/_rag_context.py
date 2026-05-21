"""
RAG context builder for chapter generation.

A single, read-only, no-LLM assembly path shared by 正文创作 generation
(`/api/generation/quick-generate`) and the prompt preview/copy flow. It
collects the structured creative context for a project — platform
directive, character cards, worldbook, selected reference-work features,
selected writing-knowledge entries, style calibration, learned
preferences, unresolved foreshadowing — into labeled, individually
token-budgeted blocks.

Each block is a complete self-contained string (with its own ``## 标题``
header) or ``""``. They are designed to be concatenated directly into the
``generation.single_agent`` prompt template, so empty blocks vanish
cleanly.

Pure and defensive: every loader is wrapped so a missing collection,
empty DB or test mode degrades to ``""`` rather than raising.
"""
from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger("inkoctobot.ui.backend.rag_context")

# ── Per-block soft character budgets (rough CJK token ≈ chars / 1.7) ──
_BUDGET = {
    "character_cards": 1800,
    "worldbook": 1600,
    "reference_summary": 2000,
    "referenced_materials": 1400,
    "writing_knowledge": 1600,
    "writing_skills": 2400,
    "project_memory": 1600,
    "foreshadowing": 800,
    "user_preferences": 800,
}


def _clip(text: str, limit: int) -> str:
    """Trim text to a soft char budget with an explicit marker."""
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "\n……（内容过长，已截断）"


def _section(label: str, body: str) -> str:
    """Wrap a non-empty body as a labeled block, or return ""."""
    body = (body or "").strip()
    if not body:
        return ""
    return f"\n\n## {label}\n{body}"


def _coerce_json(value: Any) -> Any:
    """Parse a value that may be a JSON string, dict, list or None."""
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        try:
            return json.loads(s)
        except Exception:
            return None
    return None


# ════════════════════════════════════════════════════════════════════
# Loaders — each returns a labeled block string (or "")
# ════════════════════════════════════════════════════════════════════


def _load_platform_directive(project_id: str) -> str:
    """Resolve the project's publishing platform to a creative directive."""
    try:
        from ui.backend.app.routers.data_api import _col, _safe_id
        from analysis.feature_extraction.platform_profiles import get_platform_directive

        p = _col("projects") / f"{_safe_id(project_id)}.json"
        if not p.exists():
            return ""
        proj = json.loads(p.read_text("utf-8"))
        directive = get_platform_directive(proj.get("platform", ""))
        if not directive:
            return ""
        return _section(
            "目标平台特性",
            f"{directive}\n（以上为平台风格参考；若与本章具体指令冲突，以章节指令为准。）",
        )
    except Exception as e:
        logger.debug("platform directive skipped: %s", e)
        return ""


def _load_character_cards(project_id: str, names: list[str]) -> str:
    """Build deep character cards for the chapter's on-stage characters."""
    if not names:
        return ""
    try:
        from ui.backend.app.routers.data_api import _list

        rows = _list("characters", filter_key="project_id", filter_value=project_id)
        by_name = {r.get("name", ""): r for r in rows}
        cards: list[str] = []
        for name in names:
            c = by_name.get(name)
            if not c:
                continue
            lines = [f"【{name}】" + (f"（{c['role']}）" if c.get("role") else "")]
            if c.get("appearance"):
                lines.append(f"  外貌：{c['appearance']}")
            if c.get("personality"):
                lines.append(f"  性格：{c['personality']}")
            if c.get("background"):
                lines.append(f"  背景：{c['background']}")
            if c.get("speech_style"):
                lines.append(f"  说话风格：{c['speech_style']}")
            rels = c.get("relationships") or []
            if isinstance(rels, list) and rels:
                rel_txt = "；".join(
                    f"{r.get('target_name', '')}"
                    + (f"（{r.get('label')}）" if r.get("label") else "")
                    for r in rels[:6]
                    if r.get("target_name")
                )
                if rel_txt:
                    lines.append(f"  关系：{rel_txt}")
            cards.append("\n".join(lines))
        if not cards:
            return ""
        return _section("出场角色档案", _clip("\n\n".join(cards), _BUDGET["character_cards"]))
    except Exception as e:
        logger.debug("character cards skipped: %s", e)
        return ""


def _load_worldbook(project_id: str) -> str:
    """Collect the project's worldbook entries."""
    try:
        from ui.backend.app.routers.data_api import _list

        rows = _list("worldbook", filter_key="project_id", filter_value=project_id)
        if not rows:
            return ""
        entries: list[str] = []
        for e in rows:
            title = (e.get("title") or "").strip()
            content = (e.get("content") or "").strip()
            if not title and not content:
                continue
            cat = (e.get("category") or "").strip()
            head = f"【{title}】" + (f"（{cat}）" if cat else "")
            entries.append(f"{head} {content}".strip())
        if not entries:
            return ""
        return _section("世界观设定（世界书）", _clip("\n".join(entries), _BUDGET["worldbook"]))
    except Exception as e:
        logger.debug("worldbook skipped: %s", e)
        return ""


def _ref_selection(project_id: str) -> dict:
    """Load the per-project reference-work × feature-type selection map."""
    try:
        from ui.backend.app.routers.data_api import _col, _safe_id

        p = _col("reference_injection") / f"{_safe_id(project_id)}.json"
        if not p.exists():
            return {}
        data = json.loads(p.read_text("utf-8"))
        sel = data.get("selections")
        return sel if isinstance(sel, dict) else {}
    except Exception:
        return {}


def _condense_ref_characters(raw: Any) -> str:
    data = _coerce_json(raw)
    chars = data.get("characters") if isinstance(data, dict) else data
    if not isinstance(chars, list):
        return ""
    out: list[str] = []
    for c in chars[:5]:
        if not isinstance(c, dict):
            continue
        name = (c.get("name") or "").strip()
        if not name:
            continue
        intro = (c.get("intro") or "").strip()
        tag = (c.get("role_tag") or "").strip()
        line = f"- {name}" + (f"（{tag}）" if tag else "")
        if intro:
            line += f"：{intro}"
        out.append(line)
    return "角色原型：\n" + "\n".join(out) if out else ""


def _condense_ref_settings(raw: Any) -> str:
    data = _coerce_json(raw)
    items = data.get("settings") if isinstance(data, dict) else data
    if not isinstance(items, list):
        return ""
    out: list[str] = []
    for s in items[:6]:
        if not isinstance(s, dict):
            continue
        title = (s.get("title") or "").strip()
        summary = (s.get("summary") or s.get("content") or "").strip()
        if not title and not summary:
            continue
        out.append(f"- {title}：{summary}".strip("：").strip())
    return "世界设定：\n" + "\n".join(out) if out else ""


def _condense_ref_plot(raw: Any) -> str:
    data = _coerce_json(raw)
    if not isinstance(data, dict):
        return ""
    parts: list[str] = []
    logline = (data.get("logline") or "").strip()
    if logline:
        parts.append(f"主线：{logline}")
    epochs = data.get("epochs")
    if isinstance(epochs, list) and epochs:
        titles = [
            (e.get("title") or "").strip()
            for e in epochs[:8]
            if isinstance(e, dict) and (e.get("title") or "").strip()
        ]
        if titles:
            parts.append("阶段：" + " → ".join(titles))
    return "剧情结构：\n" + "\n".join(parts) if parts else ""


def _condense_ref_rhythm(raw: Any, style_fp: Any) -> str:
    data = _coerce_json(raw)
    parts: list[str] = []
    if isinstance(data, dict):
        op = (data.get("opening_pattern") or "").strip()
        if op:
            parts.append(f"开篇方式：{op}")
        segs = data.get("pacing_segments")
        if isinstance(segs, list) and segs:
            kinds = [s.get("pacing", "") for s in segs if isinstance(s, dict)]
            kinds = [k for k in kinds if k]
            if kinds:
                parts.append("节奏分布：" + "、".join(kinds))
    fp = _coerce_json(style_fp)
    if isinstance(fp, dict):
        bits: list[str] = []
        if fp.get("dialogue_ratio") is not None:
            try:
                bits.append(f"对话比 {float(fp['dialogue_ratio']):.0%}")
            except Exception:
                pass
        if fp.get("description_density") is not None:
            try:
                bits.append(f"描写密度 {float(fp['description_density']):.0%}")
            except Exception:
                pass
        if bits:
            parts.append("文风指纹：" + "、".join(bits))
    return "叙事节奏：\n" + "\n".join(parts) if parts else ""


def _load_reference_blocks(project_id: str, db_path: str) -> str:
    """Inject the user-selected reference-work × feature-type material."""
    selection = _ref_selection(project_id)
    if not selection:
        return ""
    try:
        from rag.reference_db import ReferenceDB

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
                condensed.append(_condense_ref_characters(work.get("extracted_characters_json")))
            if feats.get("settings"):
                condensed.append(_condense_ref_settings(work.get("settings_json")))
            if feats.get("plot"):
                condensed.append(_condense_ref_plot(work.get("plot_outline_json")))
            if feats.get("rhythm"):
                condensed.append(
                    _condense_ref_rhythm(
                        work.get("rhythm_json"), work.get("style_fingerprint_json")
                    )
                )
            condensed = [c for c in condensed if c]
            if condensed:
                blocks.append(f"《{title}》\n" + "\n".join(condensed))
        if not blocks:
            return ""
        body = _clip("\n\n".join(blocks), _BUDGET["reference_summary"])
        return _section(
            "参考作品借鉴（仅作创作借鉴，不可照抄）", body
        )
    except Exception as e:
        logger.debug("reference blocks skipped: %s", e)
        return ""


def _load_writing_knowledge(project_id: str) -> str:
    """Inject the writing-knowledge entries selected for this project."""
    try:
        from ui.backend.app.routers.data_api import _col, _safe_id, _list

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
            title = (k.get("title") or "").strip()
            content = (k.get("content") or "").strip()
            if not content:
                continue
            domain = (k.get("domain") or "").strip()
            head = f"【{title}】" + (f"（{domain}）" if domain else "")
            out.append(f"{head} {content}".strip())
        if not out:
            return ""
        body = _clip("\n".join(out), _BUDGET["writing_knowledge"])
        return _section(
            "专业写作知识（世界观与设定须与之严谨一致）", body
        )
    except Exception as e:
        logger.debug("writing knowledge skipped: %s", e)
        return ""


def _load_style_calibration(project_id: str) -> str:
    """Build a style note from the project's风格校准 settings."""
    try:
        from ui.backend.app.routers.data_api import _col, _safe_id

        p = _col("calibration") / f"{_safe_id(project_id)}.json"
        if not p.exists():
            return ""
        cal = json.loads(p.read_text("utf-8"))
        sp = cal.get("style_params") or {}
        if not sp:
            return ""
        tone = sp.get("tone", 50)
        pacing = sp.get("pacing", 50)
        rhetoric = sp.get("rhetoric", 50)
        persp = sp.get("perspective", "third")
        aud = sp.get("audience", "general")
        tone_d = "轻松幽默" if tone < 30 else ("严肃深沉" if tone > 70 else "均衡")
        pacing_d = "快节奏" if pacing < 30 else ("慢热" if pacing > 70 else "中等节奏")
        rhet_d = "白描直接" if rhetoric < 30 else ("华丽修辞" if rhetoric > 70 else "适度修辞")
        persp_d = {"first": "第一人称", "third": "第三人称", "omniscient": "全知视角"}.get(persp, persp)
        aud_d = {"male": "男频", "female": "女频", "general": "大众"}.get(aud, aud)
        note = f"文风：{tone_d}；节奏：{pacing_d}；修辞：{rhet_d}；视角：{persp_d}；受众：{aud_d}"
        return _section("文风校准", note)
    except Exception as e:
        logger.debug("style calibration skipped: %s", e)
        return ""


def _load_foreshadowing(project_id: str, db_path: str, chapter_num: int) -> str:
    try:
        from ui.backend.app.routers.generation_api import _load_unresolved_foreshadowing

        txt = _load_unresolved_foreshadowing(project_id, db_path, chapter_num)
        if not txt:
            return ""
        return _section("未回收伏笔提醒", txt)
    except Exception as e:
        logger.debug("foreshadowing skipped: %s", e)
        return ""


def _load_user_preferences(project_id: str, db_path: str) -> str:
    try:
        from ui.backend.app.routers.generation_api import _load_user_style_preferences

        txt = _load_user_style_preferences(project_id, db_path)
        if not txt:
            return ""
        return _section("用户写作偏好（从历史修改中学习）", _clip(txt, _BUDGET["user_preferences"]))
    except Exception as e:
        logger.debug("user preferences skipped: %s", e)
        return ""


# ════════════════════════════════════════════════════════════════════
# Public API
# ════════════════════════════════════════════════════════════════════


def build_generation_context(
    project_id: str,
    chapter_num: int = 1,
    characters: list[str] | None = None,
    db_path: str | None = None,
) -> dict:
    """Assemble the RAG context for a chapter-generation call.

    Returns ``{"blocks": {...}, "sections": [{label, content}],
    "token_estimate": int}``. Every block value is either ``""`` or a
    self-contained ``\\n\\n## 标题\\n...`` string ready to splice into the
    ``generation.single_agent`` template.
    """
    characters = characters or []
    if db_path is None:
        try:
            from ui.backend.app.routers.generation_api import _get_db_path

            db_path = _get_db_path()
        except Exception:
            db_path = ""

    blocks: dict[str, str] = {
        "platform_directive": _load_platform_directive(project_id),
        "style_calibration": _load_style_calibration(project_id),
        "project_memory": _load_project_memory(project_id),
        "character_cards": _load_character_cards(project_id, characters),
        "worldbook": _load_worldbook(project_id),
        "reference_summary": _load_reference_blocks(project_id, db_path or ""),
        "writing_knowledge": _load_writing_knowledge(project_id),
        "writing_skills": _load_writing_skills(),
        "foreshadowing": _load_foreshadowing(project_id, db_path or "", chapter_num),
        "user_preferences": _load_user_preferences(project_id, db_path or ""),
    }

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


def build_skills_block(skills: list[str] | None) -> str:
    """Format the user-selected writing skills into a prompt block."""
    names = [str(s).strip() for s in (skills or []) if str(s).strip()]
    if not names:
        return ""
    body = "请在创作中运用以下写作技能：\n" + "\n".join(f"- {n}" for n in names)
    return _section("写作技能", body)


def build_referenced_materials_block(
    events: list[dict] | None,
    inspirations: list[dict] | None,
) -> str:
    """Format the chapter's linked chronicle events + inspirations into a
    prompt block so single-agent generation can draw on them as reference."""
    lines: list[str] = []
    for e in (events or []):
        wt = str(e.get("work_title") or e.get("ref_title") or "").strip()
        nm = str(e.get("name") or "").strip()
        desc = str(e.get("description") or "").strip()
        head = (f"《{wt}》{nm}" if wt else nm).strip()
        if not head and not desc:
            continue
        lines.append(f"- 参考事件 {head}：{desc}" if desc else f"- 参考事件 {head}")
    for ins in (inspirations or []):
        t = str(ins.get("title") or "").strip()
        c = str(ins.get("content") or "").strip()
        if not t and not c:
            continue
        lines.append(f"- 关联灵感 {t}：{c}" if t else f"- 关联灵感 {c}")
    if not lines:
        return ""
    body = _clip("\n".join(lines), _BUDGET["referenced_materials"])
    return _section("关联参考事件与灵感", body)


def _load_writing_skills() -> str:
    """Inject the active learned skills (Claude-style SKILL.md) so the
    generating model can self-select and apply relevant writing techniques.

    Only user-created learned skills are injected — built-in extraction /
    evaluation skills are not writing techniques. The generating model
    decides which of the listed skills apply to the current chapter."""
    try:
        from ui.backend.app.routers.skill_api import (
            _get_registry, _get_deactivated, _skill_public_dict,
        )
        registry = _get_registry()
        deactivated = _get_deactivated()
        parts: list[str] = []
        for skill in registry._skills.values():
            try:
                info = _skill_public_dict(skill, deactivated)
            except Exception:
                continue
            if not info.get("is_learned") or not info.get("active"):
                continue
            name = str(info.get("display_name") or info.get("name") or "").strip()
            if not name:
                continue
            desc = str(info.get("description") or "").strip()
            body = str(info.get("skill_md") or "").strip()
            seg = f"### {name}"
            if desc:
                seg += f"\n{desc}"
            if body:
                seg += f"\n{body}"
            parts.append(seg)
        if not parts:
            return ""
        body = _clip("\n\n".join(parts), _BUDGET["writing_skills"])
        return _section(
            "可用创作技能（请自动判断本章适用哪些技能并运用，无需全部使用）", body,
        )
    except Exception as e:
        logger.debug("writing skills block skipped: %s", e)
        return ""


def _load_project_memory(project_id: str) -> str:
    """Inject the project's shared memory — confirmed facts / decisions
    that persist across every AI conversation in the project."""
    try:
        from ui.backend.app.routers.data_api import _col, _safe_id

        p = _col("project_memory") / f"{_safe_id(project_id)}.json"
        if not p.exists():
            return ""
        data = json.loads(p.read_text("utf-8"))
        lines = [
            f"- {str(m.get('content') or '').strip()}"
            for m in data.get("memories", [])
            if str(m.get("content") or "").strip()
        ]
        if not lines:
            return ""
        body = _clip("\n".join(lines), _BUDGET["project_memory"])
        return _section(
            "项目记忆（贯穿本项目所有 AI 对话的已确认信息，须与之保持一致）", body,
        )
    except Exception as e:
        logger.debug("project memory skipped: %s", e)
        return ""


def load_project_memory_block(project_id: str) -> str:
    """Public accessor for the project-memory block (used by chat endpoints)."""
    return _load_project_memory(project_id)


def load_chapter_fields(project_id: str, chapter_id: str) -> dict:
    """Read a chapter's local fields (outline / time / location / characters /
    existing text) from the editor data file."""
    fields: dict[str, Any] = {
        "synopsis": "", "time_setting": "", "location": "",
        "characters": [], "existing_content": "",
    }
    if not chapter_id:
        return fields
    try:
        from ui.backend.app.routers.data_api import _col, _safe_id

        p = _col("editor") / f"{_safe_id(project_id)}.json"
        if not p.exists():
            return fields
        data = json.loads(p.read_text("utf-8"))
        for vol in data.get("volumes", []):
            for ch in vol.get("chapters", []):
                if ch.get("id") == chapter_id:
                    fields["synopsis"] = ch.get("synopsis", "") or ""
                    fields["time_setting"] = ch.get("time", "") or ""
                    fields["location"] = ch.get("location", "") or ""
                    fields["characters"] = ch.get("characters", []) or []
                    fields["existing_content"] = ch.get("content", "") or ""
                    return fields
    except Exception as e:
        logger.debug("load chapter fields skipped: %s", e)
    return fields


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
) -> dict:
    """Assemble the full variable dict for the ``generation.single_agent``
    template — RAG blocks plus chapter-local blocks. Shared by
    ``/quick-generate`` and the prompt preview endpoint so the previewed,
    copied and generated prompt are identical."""
    characters = characters or []
    ctx = build_generation_context(project_id, chapter_num, characters, db_path=db_path)
    blocks: dict[str, str] = dict(ctx["blocks"])

    synopsis = (synopsis or "").strip()
    blocks["chapter_outline"] = _section(
        "章节大纲",
        synopsis or "（未提供章节大纲，请根据已有正文与设定合理推进剧情）",
    )

    tl: list[str] = []
    if time_setting:
        tl.append(f"时间：{time_setting}")
    if location:
        tl.append(f"地点：{location}")
    blocks["time_location"] = _section("时间与地点", "\n".join(tl))

    aliases = character_aliases or {}
    if characters:
        display = "、".join(aliases.get(c, c) for c in characters)
        blocks["characters_block"] = _section(
            "本章出场角色",
            f"{display}\n仅允许上述角色出场，禁止引入未列出的角色。",
        )
    else:
        blocks["characters_block"] = ""

    existing = (existing_content or "").strip()
    blocks["existing_content"] = (
        _section("已有正文（需在此基础上续写，保持风格一致）", existing[-800:])
        if len(existing) > 10
        else ""
    )

    # Active learned skills (from build_generation_context) take priority;
    # fall back to an explicit name list when no learned skills are active.
    blocks["skills_block"] = blocks.pop("writing_skills", "") or build_skills_block(skills)
    blocks["referenced_materials"] = build_referenced_materials_block(
        referenced_events, referenced_inspirations,
    )
    return blocks
