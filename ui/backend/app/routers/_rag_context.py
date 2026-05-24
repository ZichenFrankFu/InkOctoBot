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
    "referenced_materials": 2600,
    "writing_knowledge": 1600,
    "writing_skills": 2400,
    "project_memory": 1600,
    "adjacent_context": 1200,
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


_platform_market_cache: dict = {}


def _build_platform_market_directive(plat_code: str) -> str:
    """Run the market-database analysis panel for a platform and distil it
    into a creative directive — real genre/tag share, not a static profile."""
    import time as _t

    cached = _platform_market_cache.get(plat_code)
    if cached and _t.time() - cached[0] < 1800:
        return cached[1]
    directive = ""
    try:
        from ui.backend.app.routers.analysis_api import run_analysis

        res = run_analysis(platform=plat_code, lookback="all", top_k=20)
        if (not res.get("empty")) or plat_code == "both":
            parts: list[str] = []
            tags = [t for t in (res.get("tag_rollup") or []) if t.get("tag")]
            if tags:
                top = sorted(tags, key=lambda t: t.get("latest_share") or 0, reverse=True)[:12]
                parts.append("高份额题材标签（份额）：" + "、".join(
                    f"{t['tag']}({(t.get('latest_share') or 0):.1%})" for t in top))
            cats = [c for c in (res.get("cat_rollup") or []) if c.get("category")]
            if cats:
                topc = sorted(cats, key=lambda c: c.get("latest_count") or 0, reverse=True)[:8]
                parts.append("主流分类：" + "、".join(str(c["category"]) for c in topc))
            opps = [o for o in (res.get("opportunities") or []) if o.get("tag")]
            if opps:
                topo = sorted(opps, key=lambda o: o.get("opportunity_score") or 0, reverse=True)[:6]
                parts.append("当前开书机会（份额上升的题材）：" + "、".join(
                    f"{o.get('category') or ''}·{o['tag']}".strip("·") for o in topo))
            directive = "\n".join(parts)
    except Exception as e:
        logger.debug("platform market analysis skipped: %s", e)
    _platform_market_cache[plat_code] = (_t.time(), directive)
    return directive


def _load_platform_directive(project_id: str, exclude: set | None = None) -> str:
    """Ground the project's publishing platform in real data — the genre /
    tag share conclusions from the market-database analysis panel."""
    if exclude and "platform" in exclude:
        return ""
    try:
        from ui.backend.app.routers.data_api import _col, _safe_id

        p = _col("projects") / f"{_safe_id(project_id)}.json"
        if not p.exists():
            return ""
        proj = json.loads(p.read_text("utf-8"))
        platform = str(proj.get("platform") or "").strip()
        if not platform:
            return ""
        low = platform.lower()
        if "番茄" in platform or "fanqie" in low:
            plat_code = "fanqie"
        elif "起点" in platform or "qidian" in low:
            plat_code = "qidian"
        else:
            plat_code = "both"
        directive = _build_platform_market_directive(plat_code)
        if not directive and plat_code != "both":
            directive = _build_platform_market_directive("both")
        if not directive:
            return ""
        body = _clip(f"目标发布平台：{platform}\n{directive}", 1800)
        return _section(
            "目标平台市场特性（基于市场数据库分析面板的真实数据）",
            f"{body}\n（以上为该平台真实题材 / 份额分析；与本章具体指令冲突时以章节指令为准。）",
        )
    except Exception as e:
        logger.debug("platform directive skipped: %s", e)
        return ""


def _load_character_cards(project_id: str, names: list[str], exclude: set | None = None) -> str:
    """Build deep character cards for the chapter's on-stage characters."""
    if not names:
        return ""
    try:
        from ui.backend.app.routers.data_api import _list

        rows = _list("characters", filter_key="project_id", filter_value=project_id)
        by_name = {r.get("name", ""): r for r in rows}
        cards: list[str] = []
        for name in names:
            if exclude and name in exclude:
                continue
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


def _load_worldbook(project_id: str, exclude: set | None = None) -> str:
    """Collect the project's worldbook entries."""
    try:
        from ui.backend.app.routers.data_api import _list

        rows = _list("worldbook", filter_key="project_id", filter_value=project_id)
        if not rows:
            return ""
        entries: list[str] = []
        for e in rows:
            eid = str(e.get("id") or e.get("title") or "")
            if exclude and eid in exclude:
                continue
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
        ph: list[str] = []
        for key, label in (("payoff_density", "爽点密度"), ("hook_density", "钩子密度")):
            v = data.get(key)
            if v is not None:
                try:
                    ph.append(f"{label} {float(v):.0%}")
                except Exception:
                    pass
        if ph:
            parts.append("爽点 / 钩子：" + "、".join(ph))
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


def _load_reference_blocks(project_id: str, db_path: str, exclude: set | None = None) -> str:
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


def _load_writing_knowledge(project_id: str, exclude: set | None = None) -> str:
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


def _load_foreshadowing(project_id: str, chapter_id: str = "") -> str:
    """Inject the user-managed 伏笔 (from the 大纲 tab) linked to this
    chapter — so the model knows what to plant or pay off here."""
    if not chapter_id:
        return ""
    try:
        from ui.backend.app.routers.data_api import _col, _safe_id

        p = _col("foreshadowing") / f"{_safe_id(project_id)}.json"
        if not p.exists():
            return ""
        items = json.loads(p.read_text("utf-8")).get("items", [])
        lines: list[str] = []
        for f in items:
            if chapter_id not in (f.get("chapter_ids") or []):
                continue
            title = str(f.get("title") or "").strip()
            content = str(f.get("content") or "").strip()
            if not title and not content:
                continue
            lines.append(f"- 【{title or '伏笔'}】{content}".rstrip())
        if not lines:
            return ""
        body = _clip("\n".join(lines), _BUDGET["foreshadowing"])
        return _section("关联伏笔（本章需埋设或回收的伏笔）", body)
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


def _parse_rag_excludes(rag_excludes: list[str] | None) -> dict[str, set[str]]:
    """Parse ``["block::id", ...]`` user de-selections into ``{block: {ids}}``."""
    excl: dict[str, set[str]] = {}
    for item in (rag_excludes or []):
        s = str(item or "")
        if "::" not in s:
            continue
        blk, _, iid = s.partition("::")
        excl.setdefault(blk.strip(), set()).add(iid.strip())
    return excl


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
            from ui.backend.app.routers.generation_api import _get_db_path

            db_path = _get_db_path()
        except Exception:
            db_path = ""

    excl = _parse_rag_excludes(rag_excludes)
    blocks: dict[str, str] = {
        "platform_directive": _load_platform_directive(project_id, excl.get("platform")),
        "style_calibration": _load_style_calibration(project_id),
        "character_cards": _load_character_cards(project_id, characters, excl.get("character_cards")),
        "worldbook": _load_worldbook(project_id, excl.get("worldbook")),
        "reference_summary": _load_reference_blocks(project_id, db_path or "", excl.get("reference_summary")),
        "writing_knowledge": _load_writing_knowledge(project_id, excl.get("writing_knowledge")),
        "writing_skills": _load_writing_skills(skills),
        "adjacent_context": _load_adjacent_context(project_id, chapter_id, excl.get("adjacent_context")),
        "foreshadowing": _load_foreshadowing(project_id, chapter_id),
        "user_preferences": _load_user_preferences(project_id, db_path or ""),
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
    db_path: str = "",
) -> str:
    """Build the chapter's reference block from its 大纲-tab linked events
    and inspirations. Each referenced work is enriched from the reference
    database with its full-book plot outline, character roster and
    rhythm / payoff / hook profile."""
    by_work: dict[str, list[dict]] = {}
    for e in (events or []):
        rid = str(e.get("ref_id") or e.get("work_title") or e.get("ref_title") or "")
        by_work.setdefault(rid, []).append(e)
    rdb = None
    if db_path and by_work:
        try:
            from rag.reference_db import ReferenceDB

            rdb = ReferenceDB(db_path)
        except Exception:
            rdb = None
    blocks: list[str] = []
    for rid, evs in by_work.items():
        wt = str(evs[0].get("work_title") or evs[0].get("ref_title") or rid).strip()
        seg = [f"《{wt}》"]
        work = None
        if rdb is not None:
            try:
                work = rdb.get_work(rid)
            except Exception:
                work = None
        if work:
            for cond in (
                _condense_ref_plot(work.get("plot_outline_json")),
                _condense_ref_characters(work.get("extracted_characters_json")),
                _condense_ref_rhythm(work.get("rhythm_json"), work.get("style_fingerprint_json")),
            ):
                if cond and cond.strip():
                    seg.append(cond.strip())
        ev_lines: list[str] = []
        for e in evs:
            nm = str(e.get("name") or "").strip()
            desc = str(e.get("description") or "").strip()
            ch = str(e.get("chapter") or "").strip()
            head = nm + (f"（{ch}）" if ch else "")
            if not head and not desc:
                continue
            ev_lines.append(f"- {head}：{desc}" if desc else f"- {head}")
        if ev_lines:
            seg.append("关联事件（user 选取）：\n" + "\n".join(ev_lines))
        if len(seg) > 1:
            blocks.append("\n".join(seg))
    insp_lines: list[str] = []
    for ins in (inspirations or []):
        t = str(ins.get("title") or "").strip()
        c = str(ins.get("content") or "").strip()
        if not t and not c:
            continue
        insp_lines.append(f"- {t}：{c}" if t else f"- {c}")
    if insp_lines:
        blocks.append("关联灵感：\n" + "\n".join(insp_lines))
    if not blocks:
        return ""
    body = _clip("\n\n".join(blocks), _BUDGET["referenced_materials"])
    return _section(
        "关联参考作品与灵感（参考作品含全书剧情大纲 / 角色 / 节奏·爽点·钩子）", body,
    )


def _active_learned_skills() -> list[dict]:
    """Return the user's active learned skills as
    ``[{name, description, body}]`` — the shared source for the skill
    prompt block and the skill-name list surfaced in the UI."""
    try:
        from ui.backend.app.routers.skill_api import (
            _get_registry, _get_deactivated, _skill_public_dict,
        )
        registry = _get_registry()
        deactivated = _get_deactivated()
        out: list[dict] = []
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
            out.append({
                "name": name,
                "description": str(info.get("description") or "").strip(),
                "body": str(info.get("skill_md") or "").strip(),
            })
        return out
    except Exception as e:
        logger.debug("active learned skills skipped: %s", e)
        return []


def active_writing_skill_names(only: list[str] | None = None) -> list[str]:
    """Public: display names of the active learned skills that generation
    injects — surfaced in the UI so the user sees which skills were used.

    When ``only`` is a list the result is limited to that user selection;
    ``None`` means the user made no explicit selection → all active."""
    names = [s["name"] for s in _active_learned_skills()]
    if only is not None:
        wanted = set(only)
        names = [n for n in names if n in wanted]
    return names


def _load_writing_skills(only: list[str] | None = None, web_mode: bool = False) -> str:
    """Inject the active learned skills (Claude-style SKILL.md).

    ``web_mode`` builds the block for a copy-to-web-LLM prompt: it lists
    the skills by name and asks the LLM to confirm it has each skill's
    SKILL.md (the user downloads + uploads them); the default mode inlines
    the full SKILL.md body for a direct API call. ``only`` is the user's
    explicit selection (``None`` = inject every active skill)."""
    skills = _active_learned_skills()
    if only is not None:
        wanted = set(only)
        skills = [s for s in skills if s["name"] in wanted]
    if not skills:
        return _section(
            "可用创作技能（Skill Access：无）",
            "本次创作未加载任何自定义创作技能，按下方通用写作要求创作即可。",
        )
    if web_mode:
        names = "、".join(s["name"] for s in skills)
        body = (
            f"本次创作需运用以下创作技能：{names}。\n"
            "【Skill Access 检查】这些技能的 SKILL.md 文件应已由用户下载并上传给你。"
            "开始创作前，请逐一确认你是否能访问每个技能：\n"
            "- 可访问：严格按该技能 SKILL.md 的指引创作；\n"
            "- 不可访问（用户未上传）：请在正文最前面用「【缺失技能：技能名】」明确标注，"
            "再尽量依据技能名称与已有信息创作。\n"
            "技能指令为写作的最高准则，与通用写作要求冲突时一律以技能为准。"
        )
        return _section("可用创作技能（Skill Access：需确认）", body)
    parts: list[str] = []
    for s in skills:
        seg = f"### {s['name']}"
        if s["description"]:
            seg += f"\n{s['description']}"
        if s["body"]:
            seg += f"\n{s['body']}"
        parts.append(seg)
    body = _clip("\n\n".join(parts), _BUDGET["writing_skills"])
    directive = (
        f"【写作准则】本次创作已加载 {len(skills)} 个创作技能（Skill Access：可用）。"
        "请先判断各技能是否适用于本章，对适用的技能严格执行其指引；"
        "当技能指令与通用写作要求冲突时，一律以技能为准。\n\n"
    )
    return _section(
        f"可用创作技能（Skill Access：已加载 {len(skills)} 项）",
        directive + body,
    )


def build_rag_digest(
    project_id: str, chapter_num: int = 1, characters: list[str] | None = None,
    chapter_id: str = "", rag_excludes: list[str] | None = None,
) -> str:
    """Fold the project's RAG blocks (memory / character cards / worldbook /
    references / writing knowledge / adjacent chapters) into one grounding
    digest — used to give the evaluator the same project context the
    generation step had."""
    try:
        ctx = build_generation_context(
            project_id, chapter_num, characters or [],
            chapter_id=chapter_id, rag_excludes=rag_excludes)
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


def _load_project_memory(project_id: str, exclude: set | None = None) -> str:
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
            and not (exclude and str(m.get("id")) in exclude)
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


def _load_adjacent_context(
    project_id: str, chapter_id: str, exclude: set | None = None,
) -> str:
    """Inject the previous chapter's ending excerpt and the next chapter's
    outline so the new chapter flows naturally from what came before and
    sets up what follows."""
    if not chapter_id:
        return ""
    exclude = exclude or set()
    try:
        from ui.backend.app.routers.data_api import _col, _safe_id

        p = _col("editor") / f"{_safe_id(project_id)}.json"
        if not p.exists():
            return ""
        data = json.loads(p.read_text("utf-8"))
        chapters: list[dict] = []
        for vol in data.get("volumes", []):
            for ch in vol.get("chapters", []):
                chapters.append(ch)
        idx = next(
            (i for i, ch in enumerate(chapters) if ch.get("id") == chapter_id), -1,
        )
        if idx < 0:
            return ""
        parts: list[str] = []
        if idx > 0 and "prev" not in exclude:
            prev = chapters[idx - 1]
            prev_text = (prev.get("content") or "").strip()
            if prev_text:
                tail = prev_text[-800:]
                if len(prev_text) > 800:
                    tail = "……" + tail
                parts.append(
                    f"【前一章结尾】（{prev.get('title') or '上一章'}）\n{tail}"
                )
        if idx + 1 < len(chapters) and "next" not in exclude:
            nxt = chapters[idx + 1]
            nxt_outline = (nxt.get("synopsis") or "").strip()
            if nxt_outline:
                parts.append(
                    f"【后一章大纲】（{nxt.get('title') or '下一章'}）\n{nxt_outline}"
                )
        if not parts:
            return ""
        body = _clip("\n\n".join(parts), _BUDGET["adjacent_context"])
        return _section(
            "上下文衔接（须与前一章结尾自然承接，并为后一章大纲预留铺垫）", body,
        )
    except Exception as e:
        logger.debug("adjacent context skipped: %s", e)
        return ""


def load_chapter_fields(project_id: str, chapter_id: str) -> dict:
    """Read a chapter's local fields (outline / time / location / characters /
    existing text) from the editor data file."""
    fields: dict[str, Any] = {
        "synopsis": "", "time_setting": "", "location": "",
        "characters": [], "existing_content": "",
        "referenced_events": [], "referenced_inspirations": [],
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
                    fields["referenced_events"] = ch.get("referenced_events", []) or []
                    fields["referenced_inspirations"] = ch.get("referenced_inspirations", []) or []
                    return fields
    except Exception as e:
        logger.debug("load chapter fields skipped: %s", e)
    return fields


def _creation_default_skills(mode: str = "cluster") -> list[dict]:
    """Built-in skills invoked by the given creation mode, tagged with the
    pipeline step that calls them — single-agent creation uses just the
    writing skill; the cluster pipeline calls each step's own skill."""
    _STEP = {
        "scene_direct": "场景导演", "actor_perform": "演员",
        "editor_write": "编辑撰写", "repetition_detect": "评估",
        "quality_score": "评估", "consistency_check": "评估",
        "slop_detect": "评估", "style_drift_detect": "评估",
    }
    try:
        from ui.backend.app.routers.skill_api import (
            _get_registry, _get_deactivated, _skill_public_dict,
        )
        reg = _get_registry()
        deact = _get_deactivated()
        if mode == "single":
            want_names, want_domains = {"editor_write"}, set()
        elif mode == "eval":
            want_names, want_domains = set(), {"evaluation"}
        else:
            want_names, want_domains = set(), {"production", "evaluation"}
        out: list[dict] = []
        for sk in reg._skills.values():
            try:
                info = _skill_public_dict(sk, deact)
            except Exception:
                continue
            if info.get("is_learned"):
                continue
            nm = str(info.get("name") or "")
            dom = info.get("agent_domain") or ""
            if not (nm in want_names or dom in want_domains):
                continue
            name = str(info.get("display_name") or info.get("name") or "").strip()
            if name:
                out.append({"name": name, "domain": dom,
                            "step": _STEP.get(nm, "默认")})
        return out
    except Exception as e:
        logger.debug("creation default skills skipped: %s", e)
        return []


def _project_writing_knowledge(project_id: str) -> list[dict]:
    """Writing-knowledge entries injected for the project."""
    try:
        from ui.backend.app.routers.data_api import _col, _safe_id, _list

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


def creation_context_manifest(
    project_id: str, chapter_id: str = "", chapter_num: int = 1, mode: str = "cluster",
) -> dict:
    """Summarize the skills + RAG a chapter generation will use, with the
    concrete items behind each RAG category so the creation tab can show
    them and let the user de-select individual items."""
    from ui.backend.app.routers.data_api import _col, _safe_id, _list

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
        from rag.reference_db import ReferenceDB
        from ui.backend.app.routers.generation_api import _get_db_path

        rdb = ReferenceDB(_get_db_path())
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
        "default_skills": _creation_default_skills(mode),
        "learned_skills": [
            {"name": s["name"], "description": s["description"], "skill_md": s["body"]}
            for s in _active_learned_skills()
        ],
        "writing_knowledge": _project_writing_knowledge(project_id),
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
    """Assemble the full variable dict for the ``generation.single_agent``
    template — RAG blocks plus chapter-local blocks. Shared by
    ``/quick-generate`` and the prompt preview endpoint so the previewed,
    copied and generated prompt are identical."""
    characters = characters or []
    if db_path is None:
        try:
            from ui.backend.app.routers.generation_api import _get_db_path

            db_path = _get_db_path()
        except Exception:
            db_path = ""
    ctx = build_generation_context(
        project_id, chapter_num, characters, db_path=db_path, skills=skills,
        chapter_id=chapter_id, rag_excludes=rag_excludes)
    blocks: dict[str, str] = dict(ctx["blocks"])
    _excl = _parse_rag_excludes(rag_excludes)

    synopsis = (synopsis or "").strip()
    if "__all__" in _excl.get("chapter_outline", set()):
        blocks["chapter_outline"] = ""
    else:
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
    # web_mode rebuilds the block as a Skill-Access check for a copy-to-web prompt.
    blocks["skills_block"] = blocks.pop("writing_skills", "") or build_skills_block(skills)
    if web_mode:
        blocks["skills_block"] = _load_writing_skills(skills, web_mode=True)
    blocks["referenced_materials"] = build_referenced_materials_block(
        referenced_events, referenced_inspirations, db_path or "",
    )
    return blocks
