"""Skill-registry introspection helpers used by the creation manifest.

The v3.1 ``writing_skills`` prompt block was removed — the new
``loaders/skills.py`` (LOADER_SPEC Loader 14) owns prompt-side skill
injection now. The helpers below are still here because the creation
manifest UI needs them to surface "which skills are active" /
"default skills for this mode" without re-rendering the prompt block.
"""
from __future__ import annotations

import logging

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
