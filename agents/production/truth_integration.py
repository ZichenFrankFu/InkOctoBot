"""
Writer × Truth Files integration (per docs/truth_file_system.md §9).

Three plug points exposed as standalone helpers — kept out of writer.py
so Writer stays focused on prose generation. The orchestration layer
(``/api/generation/single-writer`` endpoint, ``/api/generation/start``
pipeline) wires these in around the Writer call.

The three integration points from the roadmap doc:

1. **Phase 1 — prompt assembly** (``build_truth_context``)
     Pulls the 7-file markdown bundle for the (chapter_num, characters)
     scope. Caller passes the result into Writer.write_chapter or
     Writer.assemble_chapter as ``memory_context``.

2. **Pressured-hook injection** (``list_pressured_hooks_text``)
     One-liner reminder of hooks that have gone N+ chapters without
     advancing. Appended to the Writer's narrative_instructions so the
     LLM knows what overdue threads to address.

3. **Phase 2 — settlement output** (``extract_and_apply_truth_deltas``)
     After the Writer produces the chapter text, an LLM call extracts
     structured TruthDeltas (StatePatches / HookDeltas /
     EmotionArcEntries / RelationUpdates / ChapterSummaryDelta) from
     the prose and applies via TruthFileStore.apply_deltas (atomic,
     idempotent, validator-gated).

     Returns the ApplyResult so the caller can surface
     ``cross_ref_issues`` (the **audit gate**).

The module degrades gracefully: if Truth Files aren't seeded for a
project, each helper returns an empty result rather than raising.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from knowledge.truth.schemas import (
    ChapterSummaryDelta,
    EmotionArcEntry,
    HookDelta,
    HookImportance,
    RelationUpdate,
    StatePatch,
    TruthDeltas,
    TruthFileKind,
)
from knowledge.truth.store import TruthFileStore
from llm.base import LLMMessage

logger = logging.getLogger("inkoctobot.agents.production.truth_integration")


# ─────────────── Phase 1: prompt assembly ──────────────────────────


def build_truth_context(
    project_id: str,
    db_path: str,
    chapter_num: int,
    characters: list[str] | None = None,
    *,
    budgets_per_kind: int | None = None,
) -> str:
    """Return the 7-file Truth bundle as a single Markdown blob.

    Caller injects this into Writer.write_chapter(memory_context=...)
    or Writer.assemble_chapter(memory_context=...). The bundle includes:
    current_state / particle_ledger / pending_hooks / chapter_summaries /
    subplot_board / emotional_arcs / character_matrix.

    Empty / nonexistent Truth files render as empty strings, so the
    bundle gracefully degrades on fresh projects.
    """
    try:
        store = TruthFileStore(project_id=project_id, db_path=db_path)
        bundle = store.render_bundle_for_prompt(
            chapter_num=chapter_num,
            characters=characters or [],
            kinds=None,
            budgets=None if budgets_per_kind is None else {
                k: budgets_per_kind for k in TruthFileKind
            },
        )
    except Exception as e:
        logger.debug("truth bundle skipped for project=%s: %s", project_id, e)
        return ""

    sections = [
        body.strip() for body in bundle.values() if (body or "").strip()
    ]
    if not sections:
        return ""
    return "\n\n".join(sections)


def list_pressured_hooks_text(
    project_id: str,
    db_path: str,
    current_chapter: int,
) -> str:
    """Return a short reminder string of hooks overdue for advancement.

    Goes into ``narrative_instructions`` so the Writer knows which
    pending hooks to push forward this chapter. Empty if no pressure.
    """
    try:
        store = TruthFileStore(project_id=project_id, db_path=db_path)
        hooks = store.list_pressured_hooks(current_chapter=current_chapter)
    except Exception as e:
        logger.debug("pressured-hooks lookup skipped: %s", e)
        return ""
    if not hooks:
        return ""
    lines = ["[需要本章推进的伏笔]"]
    for h in hooks[:10]:
        origin = h.get("origin_chapter") or "?"
        desc = (h.get("description") or "").strip()
        if desc:
            lines.append(f"- 第{origin}章埋: {desc}")
    return "\n".join(lines) if len(lines) > 1 else ""


# ─────────────── Phase 2: settlement output ────────────────────────


_SETTLEMENT_PROMPT = """\
你是一个小说事实抽取引擎。给定一章已经写好的正文（和章节元数据），请从中提取以下 5 类结构化变化：

1. **state_patches** — 角色/世界永久状态的"主-谓-宾"三元组事实
   （如"李星河"-"位置"-"K-7 矿星"；"星门"-"状态"-"激活"）
2. **hook_deltas** — 伏笔的新增 / 提及 / 推进 / 回收 / 放弃
   （只输出本章实际发生的；importance: A=核心 B=次要 C=次要细节）
3. **emotion_arc_entries** — 角色情绪轨迹的明确转变（必须 from_state → to_state，且有触发事件）
4. **character_relation_updates** — 角色之间的关系变化（sentiment_score -100~100, trust_level 0~100）
5. **chapter_summary** — 本章 300 字以内的总结 + 3-5 个关键事件

严格 JSON 输出（不带额外说明）：

```json
{
  "state_patches": [
    {"subject": "...", "predicate": "...", "object": "...", "action": "upsert"}
  ],
  "hook_deltas": [
    {"description": "...", "action": "new", "importance": "B"}
  ],
  "emotion_arc_entries": [
    {"character": "...", "from_state": "...", "to_state": "...", "trigger": "..."}
  ],
  "character_relation_updates": [
    {"character_a": "...", "character_b": "...", "relation_type": "...",
     "sentiment_score": 0, "trust_level": 0}
  ],
  "chapter_summary": {
    "summary": "...",
    "key_events": ["...", "..."],
    "pov_character": "...",
    "mood": "..."
  }
}
```

如果某类没有变化，对应字段输出空数组（[]）或省略 chapter_summary。不要凭空虚构 — 只抽取文本明确支撑的事实。
"""


async def extract_truth_deltas(
    router: Any,
    chapter_text: str,
    *,
    chapter_num: int,
    chapter_title: str = "",
    pov_character: str = "",
    agent_role: str = "consolidator",
) -> TruthDeltas | None:
    """Run a settlement LLM call → TruthDeltas.

    Returns None if parsing fails. The caller is expected to handle
    None gracefully (skip the apply_deltas step, log a warning).
    """
    meta_lines: list[str] = []
    if chapter_title:
        meta_lines.append(f"章节标题：{chapter_title}")
    if pov_character:
        meta_lines.append(f"POV 视角：{pov_character}")
    meta = "\n".join(meta_lines)

    user_content = (
        f"第 {chapter_num} 章正文：\n\n{chapter_text}\n\n"
        f"{('章节元数据：' + chr(10) + meta + chr(10) + chr(10)) if meta else ''}"
        "请按上述 schema 抽取本章的结构化变化。"
    )

    messages = [
        LLMMessage(role="system", content=_SETTLEMENT_PROMPT),
        LLMMessage(role="user", content=user_content),
    ]
    try:
        resp = await router.generate(
            agent_role=agent_role, messages=messages,
            temperature=0.2, max_tokens=4000,
        )
    except Exception as e:
        logger.warning("settlement LLM call failed: %s", e)
        return None

    parsed = _parse_settlement_response(resp.content)
    if parsed is None:
        logger.warning(
            "settlement parse failed for chapter=%d; raw_head=%r",
            chapter_num, (resp.content or "")[:300],
        )
        return None

    try:
        return _build_truth_deltas(parsed, chapter_num)
    except Exception as e:
        logger.warning("settlement → TruthDeltas conversion failed: %s", e)
        return None


def apply_truth_deltas(
    deltas: TruthDeltas,
    *,
    project_id: str,
    db_path: str,
    known_characters: set[str] | None = None,
):
    """Apply via TruthFileStore.apply_deltas. Returns ApplyResult.

    The return value is the **audit gate**: ``cross_ref_issues`` with
    ``severity == "error"`` indicates the chapter should NOT be marked
    finalized; ``severity == "warning"`` / ``"info"`` is advisory.
    """
    store = TruthFileStore(project_id=project_id, db_path=db_path)
    return store.apply_deltas(
        deltas,
        validate=True,
        known_characters=known_characters,
        allow_backfill=False,
    )


async def extract_and_apply_truth_deltas(
    router: Any,
    chapter_text: str,
    *,
    project_id: str,
    db_path: str,
    chapter_num: int,
    chapter_title: str = "",
    pov_character: str = "",
    known_characters: set[str] | None = None,
    agent_role: str = "consolidator",
) -> dict[str, Any]:
    """Convenience: settlement + apply in one call.

    Returns ``{success, applied_counts, cross_ref_issues, deltas_hash,
    idempotent_hit, error}`` — flat dict so the HTTP layer can serialize.
    """
    deltas = await extract_truth_deltas(
        router, chapter_text,
        chapter_num=chapter_num,
        chapter_title=chapter_title,
        pov_character=pov_character,
        agent_role=agent_role,
    )
    if deltas is None:
        return {
            "success": False,
            "applied_counts": {},
            "cross_ref_issues": [],
            "deltas_hash": "",
            "idempotent_hit": False,
            "error": "settlement extraction failed (LLM returned unparseable output)",
        }
    try:
        result = apply_truth_deltas(
            deltas,
            project_id=project_id,
            db_path=db_path,
            known_characters=known_characters,
        )
        return {
            "success": result.success,
            "applied_counts": result.applied_counts,
            "cross_ref_issues": [
                {"rule_id": i.rule_id, "severity": i.severity,
                 "message": i.message, "location": i.location}
                for i in result.cross_ref_issues
            ],
            "deltas_hash": result.deltas_hash,
            "idempotent_hit": result.idempotent_hit,
            "error": None,
        }
    except Exception as e:
        logger.exception("apply_deltas failed")
        return {
            "success": False, "applied_counts": {}, "cross_ref_issues": [],
            "deltas_hash": "", "idempotent_hit": False,
            "error": f"apply_deltas raised: {e}",
        }


# ─────────────── Internal helpers ──────────────────────────────────


def _parse_settlement_response(text: str) -> dict[str, Any] | None:
    """Permissive JSON parser — accepts raw, fenced, or fenced-with-prefix."""
    m = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    # Try trimming to the first '{' / last '}' for sloppy LLM output.
    first = text.find("{")
    last = text.rfind("}")
    if first != -1 and last > first:
        try:
            return json.loads(text[first:last + 1])
        except json.JSONDecodeError:
            pass
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _build_truth_deltas(parsed: dict[str, Any], chapter_num: int) -> TruthDeltas:
    """Convert the LLM's JSON to a TruthDeltas object.

    Tolerates missing fields and silently drops malformed entries
    (the alternative — raising — loses partial settlement work).
    """
    state_patches: list[StatePatch] = []
    for sp in parsed.get("state_patches") or []:
        if not isinstance(sp, dict):
            continue
        subject = (sp.get("subject") or "").strip()
        predicate = (sp.get("predicate") or "").strip()
        obj = (sp.get("object") or "").strip()
        if not subject or not predicate or not obj:
            continue
        action = sp.get("action") or "upsert"
        if action not in {"upsert", "invalidate"}:
            action = "upsert"
        state_patches.append(StatePatch(
            subject=subject, predicate=predicate, object=obj,
            action=action, valid_from_chapter=chapter_num,
        ))

    hook_deltas: list[HookDelta] = []
    for hd in parsed.get("hook_deltas") or []:
        if not isinstance(hd, dict):
            continue
        desc = (hd.get("description") or "").strip()
        action = hd.get("action") or "new"
        if action not in {"new", "mention", "progress", "resolve", "abandon"}:
            action = "new"
        if not desc:
            continue
        imp_raw = (hd.get("importance") or "B").upper()
        try:
            imp = HookImportance(imp_raw) if imp_raw in {"A", "B", "C"} else HookImportance.B
        except ValueError:
            imp = HookImportance.B
        hook_deltas.append(HookDelta(
            hook_id=hd.get("hook_id"),
            description=desc, action=action,
            importance=imp,
            expected_payoff_chapter=hd.get("expected_payoff_chapter"),
            evidence=hd.get("evidence") or "",
        ))

    emotion_entries: list[EmotionArcEntry] = []
    for ea in parsed.get("emotion_arc_entries") or []:
        if not isinstance(ea, dict):
            continue
        character = (ea.get("character") or "").strip()
        from_state = (ea.get("from_state") or "").strip()
        to_state = (ea.get("to_state") or "").strip()
        trigger = (ea.get("trigger") or "").strip()
        if not (character and from_state and to_state and trigger):
            continue
        emotion_entries.append(EmotionArcEntry(
            character=character, from_state=from_state,
            to_state=to_state, trigger=trigger,
        ))

    relation_updates: list[RelationUpdate] = []
    for ru in parsed.get("character_relation_updates") or []:
        if not isinstance(ru, dict):
            continue
        a = (ru.get("character_a") or "").strip()
        b = (ru.get("character_b") or "").strip()
        rtype = (ru.get("relation_type") or "").strip()
        if not (a and b and rtype):
            continue
        try:
            sent = int(ru.get("sentiment_score") or 0)
            trust = int(ru.get("trust_level") or 0)
        except (TypeError, ValueError):
            sent, trust = 0, 0
        sent = max(-100, min(100, sent))
        trust = max(0, min(100, trust))
        relation_updates.append(RelationUpdate(
            character_a=a, character_b=b,
            relation_type=rtype,
            sentiment_score=sent, trust_level=trust,
            note=ru.get("note") or "",
        ))

    cs = parsed.get("chapter_summary")
    chapter_summary: ChapterSummaryDelta | None = None
    if isinstance(cs, dict) and cs.get("summary"):
        chapter_summary = ChapterSummaryDelta(
            summary=str(cs.get("summary") or "").strip(),
            key_events=[str(k).strip() for k in (cs.get("key_events") or [])
                        if str(k).strip()],
            pov_character=cs.get("pov_character") or None,
            mood=cs.get("mood") or None,
        )

    return TruthDeltas(
        chapter_num=chapter_num,
        current_state_patches=state_patches,
        hook_deltas=hook_deltas,
        chapter_summary=chapter_summary,
        emotion_arc_entries=emotion_entries,
        character_relation_updates=relation_updates,
    )
