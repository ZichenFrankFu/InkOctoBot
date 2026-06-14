"""event_extractor — LLM extracts 0–5 key events per chapter.

Writes into ``episodic_events`` with ``generated_by='llm_auto'`` +
``llm_model``. Strict cap of 5 events per chapter — quality over
quantity is the spec.
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
import uuid
from typing import TYPE_CHECKING, Any

from .summarizer import _call_llm as _summarizer_call_llm   # for type ref only
from .summarizer import _extract_json, _resolve_chapter_num, _resolve_chapter_text

logger = logging.getLogger("inkoctobot.commit_pipeline.event_extractor")

if TYPE_CHECKING:
    from . import SubTaskContext


# Allowed event_type values match the existing CHECK constraint on
# episodic_events.event_type.
_ALLOWED_TYPES = ("plot", "character_change", "revelation",
                  "relationship_change", "world_change")
_ALLOWED_IMPORTANCE = ("A", "B", "C")

# The DB stores importance as INTEGER 1-5 (legacy schema); the spec
# expresses it as A/B/C for LLM-friendliness. Map at persist time.
_IMPORTANCE_TO_INT = {"A": 5, "B": 3, "C": 1}

_SYSTEM = (
    "你是小说编辑助手。从一章正文里提取最多 5 条本章的关键事件。\n"
    "严格要求：\n"
    "1. 只挑真正影响后续剧情或人物状态的事件，宁少勿多\n"
    "2. 每条事件给出：event_type / description / characters_involved / importance\n"
    "3. event_type 只能从以下选：plot / character_change / revelation / relationship_change / world_change\n"
    "4. importance 只能是 A（强）/ B（中）/ C（弱）\n"
    "5. characters_involved 是出场角色名 list\n"
    "6. 输出 JSON：{\"events\": [{...}, ...]}"
)

_USER_TMPL = (
    "章节号：第 {chapter_num} 章\n"
    "出场角色：{characters}\n\n"
    "章节正文：\n"
    "{chapter_text}\n\n"
    "请输出 JSON。如本章无关键事件可输出 {{\"events\": []}}"
)


async def _call_llm(
    chapter_text: str, chapter_num: int, characters: list[str],
    *, project_id: str = "", chapter_id: str = "", db_path: str | None = None,
) -> tuple[list[dict], str]:
    """Returns (events, model_label). Raises on parse failure or
    invalid shape so the retry path engages.

    Goes through ``LLMCallSite`` so auto/manual + tokenizer + audit
    are uniform with every other call site.
    """
    from llm.call_site import LLMCallSite
    from llm.router import ModelRouter
    cs = LLMCallSite(
        call_site_id="post_commit.event_extractor",
        primary_role="post_commit",
        parsed_target_table="episodic_events",
        default_max_tokens=1200, default_temperature=0.3,
    )
    raw = await cs.invoke(
        prompt=_USER_TMPL.format(
            chapter_num=chapter_num,
            chapter_text=chapter_text[:8000],
            characters="、".join(characters) if characters else "（未指定）",
        ),
        system=_SYSTEM,
        project_id=project_id, chapter_id=chapter_id, db_path=db_path,
    )
    parsed = _extract_json(raw)
    events_raw = parsed.get("events") or []
    if not isinstance(events_raw, list):
        raise ValueError(f"LLM events field is not a list: {events_raw!r}")
    events: list[dict] = []
    provider, model = ModelRouter().resolve_role("post_commit")
    for e in events_raw[:5]:
        if not isinstance(e, dict):
            continue
        evt_type = str(e.get("event_type") or "plot").strip()
        if evt_type not in _ALLOWED_TYPES:
            evt_type = "plot"
        importance = str(e.get("importance") or "B").strip().upper()
        if importance not in _ALLOWED_IMPORTANCE:
            importance = "B"
        desc = str(e.get("description") or "").strip()[:500]
        if not desc:
            continue
        chars = e.get("characters_involved") or []
        if not isinstance(chars, list):
            chars = []
        events.append({
            "event_type":  evt_type,
            "description": desc,
            "characters":  [str(c).strip() for c in chars if str(c).strip()],
            "importance":  importance,
        })
    return events, f"{provider}/{model}".strip("/")


def _persist(
    db_path: str, project_id: str, chapter_num: int,
    events: list[dict], llm_model: str,
) -> list[str]:
    """INSERT every event row; return generated event_ids.

    Idempotent re-extract: prior ``llm_auto`` rows for this chapter are
    replaced so a retry / re-run never duplicates L4 events. User-made
    rows (``generated_by != 'llm_auto'``) are left untouched."""
    ids: list[str] = []
    with sqlite3.connect(db_path) as con:
        con.execute(
            "DELETE FROM episodic_events "
            "WHERE project_id = ? AND chapter_num = ? "
            "AND generated_by = 'llm_auto'",
            (project_id, chapter_num),
        )
        for i, e in enumerate(events):
            eid = f"ev_{uuid.uuid4().hex[:12]}"
            con.execute(
                "INSERT INTO episodic_events "
                "(event_id, project_id, chapter_num, scene_index, "
                " event_type, description, characters_json, importance, "
                " generated_by, llm_model) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'llm_auto', ?)",
                (eid, project_id, chapter_num, i,
                 e["event_type"], e["description"],
                 json.dumps(e["characters"], ensure_ascii=False),
                 _IMPORTANCE_TO_INT.get(e["importance"], 3), llm_model),
            )
            ids.append(eid)
        con.commit()
    return ids


def _resolve_characters(ctx: "SubTaskContext") -> list[str]:
    """Pull on-stage characters from the chapter's chapter_fields."""
    try:
        from ui.backend.app.services.prompt_context.chapter_fields import (
            load_chapter_fields,
        )
        fields = load_chapter_fields(ctx.project_id, ctx.chapter_id) or {}
        return (
            (fields.get("on_stage_entities") or {}).get("characters")
            or fields.get("characters") or []
        )
    except Exception:
        return []


async def run(ctx: "SubTaskContext") -> dict[str, Any]:
    chapter_text = _resolve_chapter_text(ctx)
    if not chapter_text.strip():
        return {"status": "skipped", "reason": "empty_chapter_text"}
    chapter_num = _resolve_chapter_num(ctx)
    characters = _resolve_characters(ctx)
    events, model_label = await _call_llm(
        chapter_text, chapter_num, characters,
        project_id=ctx.project_id, chapter_id=ctx.chapter_id,
        db_path=ctx.db_path,
    )
    if not events:
        return {"status": "ok", "events": 0, "llm_model": model_label}
    ids = _persist(ctx.db_path, ctx.project_id, chapter_num, events, model_label)
    return {
        "status":    "ok",
        "events":    len(ids),
        "event_ids": ids,
        "llm_model": model_label,
    }
