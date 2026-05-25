"""Writing-skills prompt block.

Two surfaces:
- ``build_simple_block(names)`` — given a list of skill names, format a
  short directive block. Used when no learned skills are active and the
  user just wants to nudge generation toward certain techniques.
- ``load_writing_skills(only=, web_mode=)`` — inject the active learned
  skills (Claude-style SKILL.md). ``web_mode`` builds the variant for
  copy-to-web-LLM use where the user uploads the SKILL.md files.

Plus introspection helpers used by the creation manifest:
- ``active_learned_skills()`` — full skill records
- ``active_writing_skill_names(only)`` — just display names
- ``creation_default_skills(mode)`` — built-in skills per creation mode
"""
from __future__ import annotations

import logging

from .budgets import BUDGETS
from .utils import clip, section

logger = logging.getLogger("inkoctobot.services.prompt_context.skills_block")


def active_learned_skills() -> list[dict]:
    """Return the user's active learned skills as ``[{name, description, body}]``.

    Single source of truth for the skill prompt block and the
    skill-name list surfaced in the UI.
    """
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
    """Display names of the active learned skills that generation injects.

    ``only`` limits the result to the user's explicit selection;
    ``None`` means the user made no explicit selection → all active.
    """
    names = [s["name"] for s in active_learned_skills()]
    if only is not None:
        wanted = set(only)
        names = [n for n in names if n in wanted]
    return names


def build_simple_block(skills: list[str] | None) -> str:
    """Format a user-selected list of skill names into a prompt block."""
    names = [str(s).strip() for s in (skills or []) if str(s).strip()]
    if not names:
        return ""
    body = "请在创作中运用以下写作技能：\n" + "\n".join(f"- {n}" for n in names)
    return section("写作技能", body)


def load_writing_skills(only: list[str] | None = None, web_mode: bool = False) -> str:
    """Inject the active learned skills (Claude-style SKILL.md).

    ``web_mode`` builds the block for a copy-to-web-LLM prompt: it lists
    the skills by name and asks the LLM to confirm it has each skill's
    SKILL.md (the user downloads + uploads them); the default mode
    inlines the full SKILL.md body for a direct API call.
    """
    skills = active_learned_skills()
    if only is not None:
        wanted = set(only)
        skills = [s for s in skills if s["name"] in wanted]
    if not skills:
        return section(
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
        return section("可用创作技能（Skill Access：需确认）", body)
    parts: list[str] = []
    for s in skills:
        seg = f"### {s['name']}"
        if s["description"]:
            seg += f"\n{s['description']}"
        if s["body"]:
            seg += f"\n{s['body']}"
        parts.append(seg)
    body = clip("\n\n".join(parts), BUDGETS["writing_skills"])
    directive = (
        f"【写作准则】本次创作已加载 {len(skills)} 个创作技能（Skill Access：可用）。"
        "请先判断各技能是否适用于本章，对适用的技能严格执行其指引；"
        "当技能指令与通用写作要求冲突时，一律以技能为准。\n\n"
    )
    return section(
        f"可用创作技能（Skill Access：已加载 {len(skills)} 项）",
        directive + body,
    )


def creation_default_skills(mode: str = "cluster") -> list[dict]:
    """Built-in skills invoked by the given creation mode.

    Each entry is tagged with the pipeline step that calls it. Single-agent
    creation uses just the writing skill; the cluster pipeline calls each
    step's own skill; eval mode uses the evaluation skills.
    """
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
