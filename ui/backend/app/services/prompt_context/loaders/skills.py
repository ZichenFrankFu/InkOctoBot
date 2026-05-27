"""Writing-skills loader (LOADER_SPEC Loader 14).

User-pinned skills are always rendered. Remaining slots up to
``max_total`` come from cosine similarity over the chapter outline +
on-stage character names.

Pulls from the DB mirror in ``skill_index`` (kept in sync with the
filesystem ``SkillRegistry``). The legacy ``load_writing_skills``
helper in ``skills_block.py`` is preserved for the web-mode prompt
preview surface but no longer participates in build_generation_context.
"""
from __future__ import annotations

import logging
from typing import Any

from ..budget_allocator import LOADER_BUDGETS
from ..loader_protocol import LoaderPlan, make_plan
from ..utils import clip, cosine, embed_sync, section

logger = logging.getLogger("inkoctobot.services.prompt_context.skills")


_PER_SKILL_BODY_CHARS = 600


def _format_skill(skill: dict, *, source_label: str) -> str:
    head = f"**{skill.get('display_name') or skill['skill_id']}**"
    kind = skill.get("kind") or "builtin"
    head += f"（type: {kind}）"
    body = (skill.get("body_snippet") or skill.get("description") or "").strip()
    if not body:
        return head
    body = body[:_PER_SKILL_BODY_CHARS]
    return f"{head}\n{body}"


_BLOCK = "skills"


def _build(pinned: list[dict], recommended: list[dict]) -> tuple[str, str]:
    """Return (title, body). Body is empty when both lists are empty."""
    parts: list[str] = []
    if pinned:
        parts.append(f"### 用户主动选中（{len(pinned)} 项）")
        for s in pinned:
            parts.append("")
            parts.append(_format_skill(s, source_label="pinned"))
    if recommended:
        if parts:
            parts.append("")
        parts.append(f"### 系统推荐（本章相关，{len(recommended)} 项）")
        for s in recommended:
            parts.append("")
            parts.append(_format_skill(s, source_label="auto"))
    title = f"创作技能（已加载 {len(pinned) + len(recommended)} 项）"
    return title, "\n".join(parts)


def _render(pinned: list[dict], recommended: list[dict]) -> str:
    title, body = _build(pinned, recommended)
    if not body:
        return ""
    return section(title, clip(body, LOADER_BUDGETS[_BLOCK]["target"]))


def plan(
    project_id: str,
    chapter_outline: str = "",
    on_stage_characters: list[str] | None = None,
    *,
    user_pinned_skill_ids: list[str] | None = None,
    max_total: int = 5,
    min_relevance: float = 0.3,
    exclude: set | None = None,
) -> LoaderPlan | None:
    pinned, recommended = _select(
        project_id, chapter_outline, on_stage_characters or [],
        user_pinned_skill_ids or [], max_total, min_relevance,
        exclude or set(),
    )
    title, body = _build(pinned, recommended)
    return make_plan(_BLOCK, title, body)


def load(
    project_id: str,
    chapter_outline: str = "",
    on_stage_characters: list[str] | None = None,
    *,
    user_pinned_skill_ids: list[str] | None = None,
    max_total: int = 5,
    min_relevance: float = 0.3,
    exclude: set | None = None,
) -> str:
    """Render the writing-skills block."""
    p = plan(
        project_id, chapter_outline, on_stage_characters,
        user_pinned_skill_ids=user_pinned_skill_ids,
        max_total=max_total, min_relevance=min_relevance, exclude=exclude,
    )
    return p.render(p.target) if p else ""


def _select(
    project_id: str, chapter_outline: str, on_stage_characters: list[str],
    user_pinned_skill_ids: list[str], max_total: int,
    min_relevance: float, exclude: set,
) -> tuple[list[dict], list[dict]]:
    """Run the lookup + ranking. Returns (pinned, recommended)."""
    try:
        from ui.backend.app.services import skill_index
        from ui.backend.app.services.project_paths import get_db_path

        db_path = get_db_path()
        try:
            skill_index.sync_from_registry(db_path)
        except Exception as e:
            logger.debug("skill_index sync skipped: %s", e)

        all_skills = skill_index.list_skills(db_path, include_embedding=True)
        if not all_skills:
            return [], []
        all_skills = [s for s in all_skills if s["skill_id"] not in exclude]
        if not all_skills:
            return [], []

        pin_set: set[str] = set(user_pinned_skill_ids)
        try:
            pin_set.update(skill_index.list_pinned_skill_ids(db_path, project_id))
        except Exception as e:
            logger.debug("list_pinned_skill_ids failed: %s", e)

        pinned = [s for s in all_skills if s["skill_id"] in pin_set]
        candidates = [s for s in all_skills if s["skill_id"] not in pin_set]
        remaining = max(0, max_total - len(pinned))
        recommended: list[dict] = []

        if remaining > 0 and candidates:
            stale_idx: list[int] = []
            stale_texts: list[str] = []
            for i, s in enumerate(candidates):
                text = skill_index.skill_embedding_text(s)
                expected = skill_index.hash_embedding_text(text)
                if not s.get("embedding") or s.get("embedding_text_hash") != expected:
                    stale_idx.append(i)
                    stale_texts.append(text)
                    s["_text_hash"] = expected

            query = (chapter_outline or "").strip()
            if on_stage_characters:
                query = f"{query}\n人物：{','.join(on_stage_characters)}".strip()

            batch_in = ([query] if query else []) + stale_texts
            batch_out = embed_sync(batch_in) if batch_in else []
            if query and batch_out:
                query_vec = batch_out[0] or []
                stale_vecs = batch_out[1:]
            else:
                query_vec = []
                stale_vecs = batch_out

            for j, idx in enumerate(stale_idx):
                vec = stale_vecs[j] if j < len(stale_vecs) else []
                if vec:
                    candidates[idx]["embedding"] = vec
                    try:
                        skill_index.set_embedding(
                            db_path, candidates[idx]["skill_id"], vec,
                            candidates[idx]["_text_hash"],
                        )
                    except Exception as e:
                        logger.debug("persist skill embedding failed for %s: %s",
                                      candidates[idx].get("skill_id"), e)

            if not query_vec:
                recommended = candidates[:remaining]
            else:
                scored: list[tuple[float, dict]] = []
                for s in candidates:
                    vec = s.get("embedding") or []
                    score = cosine(query_vec, vec) if vec else float("-inf")
                    scored.append((score, s))
                scored.sort(key=lambda t: t[0], reverse=True)
                for score, s in scored[:remaining]:
                    if score < min_relevance:
                        break
                    recommended.append(s)

        return pinned, recommended
    except Exception as e:
        logger.debug("skills loader skipped: %s", e)
        return [], []
