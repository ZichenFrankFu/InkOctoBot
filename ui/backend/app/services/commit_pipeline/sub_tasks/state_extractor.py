"""state_extractor — LLM extracts state / ledger / emotion / entities.

Direct-write pattern (spec § 模块 5): the LLM output goes straight
into the canonical tables, with every change mirrored into
``state_change_log`` for audit. The user's review dialog is an
after-the-fact audit + CRUD surface, not an approval gate.

Layout:
- ``_extract`` calls the LLM through the fallback router.
- ``_apply_state_deltas`` walks SPO triples → ``truth_current_state``
  (uses the temporal model the schema already has: supersede prior
  row by setting ``valid_to_chapter = K - 1`` and INSERT new row at
  ``valid_from_chapter = K``).
- ``_apply_ledger_deltas`` appends ``character_ledger`` rows.
- ``_apply_emotion_deltas`` appends ``emotion_arcs`` rows
  (append-only by construction).
- ``_resolve_entities`` runs the 5-layer filter from the spec;
  passes write ``storyland_entities``, suspected duplicates write
  ``entity_review_queue``.
- ``_validate_layer1`` (script) + ``_validate_layer2`` (embedding)
  produce ``state_change_log.validation_warning`` strings.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from .summarizer import (
    _extract_json, _resolve_chapter_num, _resolve_chapter_text,
)

logger = logging.getLogger("inkoctobot.commit_pipeline.state_extractor")

if TYPE_CHECKING:
    from . import SubTaskContext


# Allowed entity types match the storyland_entities CHECK constraint.
_ALLOWED_ENTITY_TYPES = {"character", "location", "item", "organization", "concept"}

# Vocabulary for SPO predicates — keeps the LLM from inventing
# variants. Validator Layer 1 enforces this set.
_ALLOWED_PREDICATES = {
    "位于", "在", "持有", "属于", "状态",
    "境界", "情绪", "关系", "目标", "信念",
    "对象",   # generic "subject acts on object"
}

# Entity-similarity thresholds (cosine on embedding service vectors).
_ALIAS_THRESHOLD     = 0.92    # auto-merge as alias
_REVIEW_THRESHOLD    = 0.85    # send to review queue, don't auto-create


# 状态合并抽取 (spec Post-Commit·机制1)：单次 LLM 调用一并产出
# Storyland 事实 + 读者视角记忆 L4 标志性事件 + 故事线/伏笔节点标签。
_SYSTEM = (
    "你是小说世界状态提取助手。从一章正文里抽取本章发生的状态变更、"
    "标志性事件，以及伏笔与故事线的进度标签。\n"
    "输出 JSON：\n"
    "{\n"
    '  "state_deltas":   [{"subject":..., "subject_type":..., "predicate":..., "new_value":..., "trigger":...}],\n'
    '  "ledger_deltas":  [{"character":..., "category":..., "key":..., "delta":..., "trigger":...}],\n'
    '  "emotion_deltas": [{"character":..., "from_state":..., "to_state":..., "trigger":...}],\n'
    '  "new_entities":   [{"name":..., "type":..., "aliases":[...]}],\n'
    '  "events":         [{"event_type":..., "description":..., "characters_involved":[...], "importance":...}],\n'
    '  "hook_deltas":    [{"hook_id":..., "description":..., "action":..., "scale":...}],\n'
    '  "subplot_updates":[{"thread_id":..., "name":..., "action":...}]\n'
    "}\n"
    "严格要求：\n"
    "1. predicate 只能从规范词表选：位于/在/持有/属于/状态/境界/情绪/关系/目标/信念/对象\n"
    "2. entity type 只能是：character/location/item/organization/concept\n"
    "3. 如新提到的实体是已有实体的别名/简称，写在已有实体的 aliases 里，不要新建\n"
    "4. ledger.delta 是数字（+/-），代表本章变化量\n"
    "5. emotion 用简短词：平静/愤怒/恐惧/喜悦/悲伤/坚定/动摇/绝望/希望/...\n"
    "6. events 最多 5 条，只挑真正影响后续剧情或人物状态的事件；"
    "event_type 只能是 plot/character_change/revelation/relationship_change/world_change；"
    "importance 只能是 A（强）/B（中）/C（弱）\n"
    "7. hook_deltas 描述本章的伏笔动作：新埋设伏笔用 action=new（hook_id 为 null，"
    "必须给 scale：boomerang 回旋镖（3章内回收）/event_clue 事件线索（20章内）/"
    "grand_plan 大计划（100章内）/world_truth 世界真相（全书级））；"
    "推进/回收已有伏笔用 action=mention/progress/resolve 并引用所给伏笔的 hook_id\n"
    "8. subplot_updates 描述本章故事线进度：推进已有故事线用 action=advance + thread_id，"
    "完结用 action=resolve\n"
    "9. 没有变更的字段输出空 list"
)

_USER_TMPL = (
    "章节号：第 {chapter_num} 章\n"
    "已有实体（仅参考，避免重复创建）：{existing}\n"
    "未回收伏笔（引用其 hook_id）：\n{open_hooks}\n"
    "进行中故事线（引用其 thread_id）：\n{subplots}\n\n"
    "章节正文：\n{chapter_text}\n\n"
    "请输出 JSON。"
)


# ─────────── extraction ───────────


async def _call_llm(
    chapter_text: str, chapter_num: int, existing_entities: list[str],
    *, project_id: str = "", chapter_id: str = "", db_path: str | None = None,
    open_hooks: list[dict] | None = None,
    active_subplots: list[dict] | None = None,
) -> tuple[dict, str]:
    """Single merged extraction call (Post-Commit·机制1). Goes through
    LLMCallSite so auto/manual mode + audit are uniform with the rest
    of the codebase."""
    from llm.call_site import LLMCallSite
    from llm.router import ModelRouter
    cs = LLMCallSite(
        call_site_id="post_commit.state_extractor",
        primary_role="post_commit",
        parsed_target_table="truth_current_state",
        default_max_tokens=3000, default_temperature=0.2,
    )
    existing_str = "、".join(existing_entities[:50]) if existing_entities else "（无）"
    hooks_str = "\n".join(
        f"- hook_id={h['hook_id']}: {h['description'][:80]}"
        for h in (open_hooks or [])[:20]
    ) or "（无）"
    subplots_str = "\n".join(
        f"- thread_id={s['thread_id']}: {s['name']} — {(s.get('description') or '')[:60]}"
        for s in (active_subplots or [])[:15]
    ) or "（无）"
    raw = await cs.invoke(
        prompt=_USER_TMPL.format(
            chapter_num=chapter_num,
            chapter_text=chapter_text[:8000],
            existing=existing_str,
            open_hooks=hooks_str,
            subplots=subplots_str,
        ),
        system=_SYSTEM,
        project_id=project_id, chapter_id=chapter_id, db_path=db_path,
    )
    parsed = _extract_json(raw)
    provider, model = ModelRouter().resolve_role("post_commit")
    return parsed, f"{provider}/{model}".strip("/")


# ─────────── write helpers ───────────


def _log(
    con, *, project_id: str, subject: str, subject_type: str,
    predicate: str, old_value: str | None, new_value: str | None,
    change_chapter: int, trigger: str, llm_model: str,
    warning: str = "", change_type: str = "state_change",
    source: str = "llm_auto",
) -> str:
    """Insert one row into state_change_log; return log_id."""
    lid = f"sl_{uuid.uuid4().hex[:12]}"
    con.execute(
        "INSERT INTO state_change_log "
        "(log_id, project_id, subject, subject_type, predicate, "
        " old_value, new_value, change_chapter, trigger, source, "
        " change_type, llm_model_used, validation_warning) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (lid, project_id, subject, subject_type, predicate,
         old_value, new_value, change_chapter, trigger, source,
         change_type, llm_model, warning or None),
    )
    return lid


def _apply_state_deltas(
    db_path: str, project_id: str, chapter_num: int,
    deltas: list[dict], llm_model: str,
) -> list[dict]:
    """Apply state deltas to truth_current_state using the temporal
    model. For each (subject, predicate):
    - find the active row (valid_to_chapter IS NULL) and set its
      valid_to_chapter = chapter_num - 1
    - INSERT a fresh row with valid_from_chapter = chapter_num
    - mirror the transition into state_change_log
    """
    applied: list[dict] = []
    with sqlite3.connect(db_path) as con:
        for d in deltas:
            subject = (d.get("subject") or "").strip()
            subject_type = (d.get("subject_type") or "character").strip() or "character"
            predicate = (d.get("predicate") or "").strip()
            new_value = (d.get("new_value") or "").strip()
            trigger = (d.get("trigger") or "").strip()
            if not subject or not predicate or not new_value:
                continue

            warnings: list[str] = []
            if predicate not in _ALLOWED_PREDICATES:
                warnings.append(f"unknown_predicate:{predicate}")

            # Find the current active row to capture old_value + supersede.
            existing = con.execute(
                "SELECT triple_id, object FROM truth_current_state "
                "WHERE project_id = ? AND subject = ? AND predicate = ? "
                "AND valid_to_chapter IS NULL",
                (project_id, subject, predicate),
            ).fetchone()
            old_value: str | None = None
            if existing:
                old_value = existing[1]
                if old_value == new_value:
                    # No actual change — skip silently.
                    continue
                con.execute(
                    "UPDATE truth_current_state SET valid_to_chapter = ? "
                    "WHERE triple_id = ?",
                    (max(1, chapter_num - 1), existing[0]),
                )

            tid = f"tcs_{uuid.uuid4().hex[:12]}"
            con.execute(
                "INSERT INTO truth_current_state "
                "(triple_id, project_id, subject, subject_type, "
                " predicate, object, valid_from_chapter) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (tid, project_id, subject, subject_type,
                 predicate, new_value, chapter_num),
            )
            log_id = _log(
                con, project_id=project_id, subject=subject,
                subject_type=subject_type, predicate=predicate,
                old_value=old_value, new_value=new_value,
                change_chapter=chapter_num, trigger=trigger,
                llm_model=llm_model,
                warning=";".join(warnings) if warnings else "",
            )
            applied.append({
                "triple_id":   tid,
                "log_id":      log_id,
                "subject":     subject,
                "predicate":   predicate,
                "old_value":   old_value,
                "new_value":   new_value,
            })
        con.commit()
    return applied


def _apply_ledger_deltas(
    db_path: str, project_id: str, chapter_num: int,
    deltas: list[dict], llm_model: str,
) -> tuple[list[dict], list[str]]:
    """Append rows to character_ledger; compute new_value from existing
    history. Returns (applied, negative_balance_warnings)."""
    applied: list[dict] = []
    negatives: list[str] = []
    with sqlite3.connect(db_path) as con:
        for d in deltas:
            char = (d.get("character") or "").strip()
            key = (d.get("key") or d.get("item_type") or "").strip()
            category = (d.get("category") or "resource").strip()
            try:
                delta = int(d.get("delta") or 0)
            except (TypeError, ValueError):
                continue
            if not char or not key or delta == 0:
                continue
            trigger = (d.get("trigger") or "").strip()

            # Compute prior balance.
            prior = con.execute(
                "SELECT COALESCE(SUM(delta), 0) FROM character_ledger "
                "WHERE project_id = ? AND character_name = ? AND key = ?",
                (project_id, char, key),
            ).fetchone()
            new_value = int((prior[0] if prior else 0)) + delta

            op = "add" if delta > 0 else "subtract"
            lid = f"lg_{uuid.uuid4().hex[:12]}"
            con.execute(
                "INSERT INTO character_ledger "
                "(ledger_id, project_id, character_name, chapter_num, "
                " category, key, operation, delta, new_value, reason) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (lid, project_id, char, chapter_num, category, key,
                 op, abs(delta), new_value, trigger),
            )
            row = {"ledger_id": lid, "character": char, "key": key,
                   "delta": delta, "new_value": new_value, "warning": ""}
            if new_value < 0:
                row["warning"] = "negative_balance"
                negatives.append(f"{char}/{key}={new_value}")
                _log(
                    con, project_id=project_id, subject=char,
                    subject_type="character", predicate=f"ledger:{key}",
                    old_value=str(new_value - delta), new_value=str(new_value),
                    change_chapter=chapter_num, trigger=trigger,
                    llm_model=llm_model, warning="negative_balance",
                )
            applied.append(row)
        con.commit()
    return applied, negatives


def _apply_emotion_deltas(
    db_path: str, project_id: str, chapter_num: int,
    deltas: list[dict],
) -> list[dict]:
    """Append rows to emotion_arcs (append-only by construction)."""
    applied: list[dict] = []
    with sqlite3.connect(db_path) as con:
        for d in deltas:
            char = (d.get("character") or "").strip()
            to_state = (d.get("to_state") or d.get("new_emotion") or "").strip()
            from_state = (d.get("from_state") or "").strip()
            trigger = (d.get("trigger") or "").strip()
            if not char or not to_state:
                continue
            aid = f"em_{uuid.uuid4().hex[:12]}"
            con.execute(
                "INSERT INTO emotion_arcs "
                "(arc_id, project_id, character_name, chapter_num, "
                " from_state, to_state, trigger) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (aid, project_id, char, chapter_num,
                 from_state, to_state, trigger),
            )
            applied.append({
                "arc_id":     aid,
                "character":  char,
                "from_state": from_state,
                "to_state":   to_state,
            })
        con.commit()
    return applied


# ─────────── entity 5-layer filter ───────────


def _list_existing_entity_names(db_path: str, project_id: str) -> list[dict]:
    """Returns [{entity_id, name, type, aliases}] for the active project."""
    with sqlite3.connect(db_path) as con:
        rows = con.execute(
            "SELECT entity_id, name, entity_type, aliases_json "
            "FROM storyland_entities WHERE project_id = ?",
            (project_id,),
        ).fetchall()
    out: list[dict] = []
    for eid, name, etype, aliases_json in rows:
        try:
            aliases = json.loads(aliases_json or "[]") or []
        except json.JSONDecodeError:
            aliases = []
        out.append({"id": eid, "name": name, "type": etype, "aliases": aliases})
    return out


def _cosine(a, b) -> float:
    import math
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def _resolve_entities(
    db_path: str, project_id: str, chapter_id: str, chapter_num: int,
    candidates: list[dict], chapter_text: str,
) -> dict:
    """Run the 5-layer filter. Returns:
    {
      "created":      [{entity_id, name, type}],
      "merged":       [{entity_id, alias_added}],
      "review_queue": [{queue_id, candidate, similar}],
      "discarded":    [name, ...],
    }
    """
    out = {"created": [], "merged": [], "review_queue": [], "discarded": []}
    if not candidates:
        return out

    existing = _list_existing_entity_names(db_path, project_id)
    existing_names = {e["name"]: e for e in existing}
    # Build a flat name → entity_id map including aliases.
    name_to_id: dict[str, str] = {}
    for e in existing:
        name_to_id[e["name"]] = e["id"]
        for a in e["aliases"]:
            name_to_id[a] = e["id"]

    # Pre-compute embeddings for existing names AND candidate names so
    # Layer 2 only needs one batch call.
    candidate_names: list[str] = []
    for c in candidates:
        nm = (c.get("name") or "").strip()
        if nm:
            candidate_names.append(nm)
    cmp_texts = [e["name"] for e in existing] + candidate_names
    embeddings: list[list[float]] = []
    if cmp_texts:
        try:
            from ui.backend.app.services.embedding import get_embedding_service
            import numpy as np
            svc = get_embedding_service()
            raw = svc.embed_batch(cmp_texts)
            embeddings = [np.frombuffer(b, dtype="float32").tolist() for b in raw]
        except Exception as e:
            logger.debug("entity embedding lookup skipped: %s", e)
            embeddings = []

    n_existing = len(existing)
    # Mention counts in chapter text — Layer 3 threshold.
    text_lower = chapter_text  # CJK case-fold not meaningful

    with sqlite3.connect(db_path) as con:
        for ci, c in enumerate(candidates):
            name = (c.get("name") or "").strip()
            etype = (c.get("type") or "concept").strip()
            aliases = c.get("aliases") or []
            if not name:
                continue

            # Layer 4: type filter.
            if etype not in _ALLOWED_ENTITY_TYPES:
                out["discarded"].append(name)
                continue

            # Layer 1: already exists by exact name or alias?
            if name in name_to_id:
                # Add new aliases to the existing record.
                eid = name_to_id[name]
                added = _merge_aliases(con, eid, [a for a in aliases if a and a not in name_to_id])
                if added:
                    out["merged"].append({"entity_id": eid, "alias_added": added})
                continue

            # Layer 2: embedding similarity to existing entities.
            best = (0.0, None)
            if embeddings and n_existing > 0:
                cand_vec = embeddings[n_existing + ci] if (n_existing + ci) < len(embeddings) else []
                for ei, e in enumerate(existing):
                    ev = embeddings[ei] if ei < len(embeddings) else []
                    score = _cosine(cand_vec, ev)
                    if score > best[0]:
                        best = (score, e)

            if best[0] >= _ALIAS_THRESHOLD and best[1]:
                eid = best[1]["id"]
                added = _merge_aliases(con, eid, [name] + [a for a in aliases if a not in name_to_id])
                out["merged"].append({
                    "entity_id":   eid,
                    "alias_added": added,
                    "score":       round(best[0], 4),
                })
                continue
            if best[0] >= _REVIEW_THRESHOLD and best[1]:
                qid = f"erq_{uuid.uuid4().hex[:12]}"
                con.execute(
                    "INSERT INTO entity_review_queue "
                    "(queue_id, project_id, chapter_id, candidate_name, "
                    " candidate_type, candidate_aliases_json, "
                    " similar_existing_entity_id, similarity_score) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (qid, project_id, chapter_id, name, etype,
                     json.dumps(aliases, ensure_ascii=False),
                     best[1]["id"], round(best[0], 4)),
                )
                out["review_queue"].append({
                    "queue_id":  qid,
                    "candidate": name,
                    "similar":   best[1]["name"],
                    "score":     round(best[0], 4),
                })
                continue

            # Layer 3: mention threshold. New entity must be mentioned
            # at least twice OR have explicit non-empty aliases to
            # earn a row. Single-mention concepts get discarded.
            mention_count = text_lower.count(name)
            if mention_count < 2 and not aliases:
                out["discarded"].append(name)
                continue

            # Layer 5: auto-create.
            eid = f"ent_{uuid.uuid4().hex[:12]}"
            con.execute(
                "INSERT INTO storyland_entities "
                "(entity_id, project_id, name, entity_type, description, "
                " introduced_chapter, aliases_json, source, "
                " mention_count, auto_created) "
                "VALUES (?, ?, ?, ?, '', ?, ?, 'manual', ?, 1)",
                (eid, project_id, name, etype, chapter_num,
                 json.dumps(aliases, ensure_ascii=False),
                 mention_count),
            )
            out["created"].append({
                "entity_id":  eid,
                "name":       name,
                "type":       etype,
                "auto_created": True,
            })
            name_to_id[name] = eid
        con.commit()
    return out


def _merge_aliases(con, entity_id: str, new_aliases: list[str]) -> list[str]:
    """Add new aliases to an entity's aliases_json (de-duped)."""
    new_aliases = [a.strip() for a in (new_aliases or []) if a and a.strip()]
    if not new_aliases:
        return []
    row = con.execute(
        "SELECT aliases_json FROM storyland_entities WHERE entity_id = ?",
        (entity_id,),
    ).fetchone()
    try:
        current = json.loads(row[0] or "[]") if row else []
    except (json.JSONDecodeError, TypeError):
        current = []
    added: list[str] = []
    for a in new_aliases:
        if a not in current:
            current.append(a)
            added.append(a)
    if added:
        con.execute(
            "UPDATE storyland_entities SET aliases_json = ?, "
            "mention_count = mention_count + 1 "
            "WHERE entity_id = ?",
            (json.dumps(current, ensure_ascii=False), entity_id),
        )
    return added


# ─────────── main run ───────────


def _list_open_hooks(db_path: str, project_id: str) -> list[dict]:
    """Open hooks the LLM may reference for mention/progress/resolve."""
    try:
        with sqlite3.connect(db_path) as con:
            rows = con.execute(
                "SELECT hook_id, description FROM pending_hooks "
                "WHERE project_id = ? AND status IN "
                "('open','progressing','pressured','near_payoff') "
                "AND COALESCE(user_marked_fully_resolved, 0) = 0 "
                "ORDER BY origin_chapter",
                (project_id,),
            ).fetchall()
        return [{"hook_id": r[0], "description": r[1]} for r in rows]
    except sqlite3.OperationalError:
        return []


def _list_active_subplots(db_path: str, project_id: str) -> list[dict]:
    try:
        with sqlite3.connect(db_path) as con:
            rows = con.execute(
                "SELECT thread_id, name, description FROM subplot_threads "
                "WHERE project_id = ? AND status NOT IN ('resolution','dormant') "
                "ORDER BY start_chapter",
                (project_id,),
            ).fetchall()
        return [
            {"thread_id": r[0], "name": r[1], "description": r[2] or ""}
            for r in rows
        ]
    except sqlite3.OperationalError:
        return []


def _normalize_events(events_raw: Any) -> list[dict]:
    """Mirror event_extractor's normalization for the merged call."""
    _allowed_types = ("plot", "character_change", "revelation",
                      "relationship_change", "world_change")
    out: list[dict] = []
    if not isinstance(events_raw, list):
        return out
    for e in events_raw[:5]:
        if not isinstance(e, dict):
            continue
        evt_type = str(e.get("event_type") or "plot").strip()
        if evt_type not in _allowed_types:
            evt_type = "plot"
        importance = str(e.get("importance") or "B").strip().upper()
        if importance not in ("A", "B", "C"):
            importance = "B"
        desc = str(e.get("description") or "").strip()[:500]
        if not desc:
            continue
        chars = e.get("characters_involved") or e.get("characters") or []
        if not isinstance(chars, list):
            chars = []
        out.append({
            "event_type":  evt_type,
            "description": desc,
            "characters":  [str(c).strip() for c in chars if str(c).strip()],
            "importance":  importance,
        })
    return out


def _apply_hook_and_subplot_deltas(
    db_path: str, project_id: str, chapter_num: int,
    hooks_raw: Any, subplots_raw: Any,
    known_hook_ids: set[str], known_thread_ids: set[str],
) -> dict[str, int]:
    """Route hook / subplot node tags through StorylandStateStore so
    they get the canonical atomic apply (audit + idempotency +
    pressure recompute). Returns applied counts."""
    from knowledge.storyland_state.schemas import (
        HookDelta, HookScale, StorylandStateDeltas, SubplotStatus,
        SubplotUpdate,
    )
    from knowledge.storyland_state.store import StorylandStateStore

    hook_deltas: list[HookDelta] = []
    for h in (hooks_raw if isinstance(hooks_raw, list) else []):
        if not isinstance(h, dict):
            continue
        action = str(h.get("action") or "").strip()
        desc = str(h.get("description") or "").strip()
        hook_id = (str(h.get("hook_id")).strip()
                   if h.get("hook_id") not in (None, "", "null") else None)
        if action == "new":
            if not desc:
                continue
            scale_raw = str(h.get("scale") or "event_clue").strip()
            try:
                scale = HookScale(scale_raw)
            except ValueError:
                scale = HookScale.event_clue
            hook_deltas.append(HookDelta(
                description=desc, action="new", scale=scale,
            ))
        elif action in ("mention", "progress", "resolve", "abandon"):
            if not hook_id or hook_id not in known_hook_ids:
                continue   # hallucinated id — skip silently
            hook_deltas.append(HookDelta(
                hook_id=hook_id, description=desc or hook_id, action=action,
            ))

    subplot_updates: list[SubplotUpdate] = []
    _status_after = {
        "new":     SubplotStatus.setup,
        "advance": SubplotStatus.building,
        "climax":  SubplotStatus.climax,
        "resolve": SubplotStatus.resolution,
        "dormant": SubplotStatus.dormant,
    }
    for s in (subplots_raw if isinstance(subplots_raw, list) else []):
        if not isinstance(s, dict):
            continue
        action = str(s.get("action") or "").strip()
        if action not in _status_after:
            continue
        thread_id = (str(s.get("thread_id")).strip()
                     if s.get("thread_id") not in (None, "", "null") else None)
        name = str(s.get("name") or "").strip()
        if action == "new":
            if not name:
                continue
            thread_id = None
        elif not thread_id or thread_id not in known_thread_ids:
            continue
        subplot_updates.append(SubplotUpdate(
            thread_id=thread_id, name=name or (thread_id or ""),
            action=action, status_after=_status_after[action],
        ))

    if not hook_deltas and not subplot_updates:
        return {"hooks": 0, "subplots": 0}
    store = StorylandStateStore(project_id, db_path=db_path)
    result = store.apply_deltas(
        StorylandStateDeltas(
            chapter_num=chapter_num,
            hook_deltas=hook_deltas,
            subplot_updates=subplot_updates,
        ),
        allow_backfill=True,
    )
    counts = result.applied_counts or {}
    return {
        "hooks": counts.get("pending_hooks", 0),
        "subplots": counts.get("subplot_threads", 0),
    }


def _require_confirmation() -> bool:
    """spec Post-Commit·机制3 / LLM交互·机制2: with this settings
    toggle on, extraction results become a pending proposal the user
    must apply (or discard) before they reach the canonical tables.
    Default off — direct-write + after-the-fact review remains the
    transitional default until the editor's Post-Commit section ships."""
    try:
        import json as _json
        from ui.backend.app.settings import settings as _s
        path = _s.get_data_path("settings.json")
        if path.exists():
            blob = _json.loads(path.read_text("utf-8"))
            return bool(blob.get("post_commit_require_confirmation"))
    except Exception:
        pass
    return False


def _store_proposal(
    ctx: "SubTaskContext", chapter_num: int, parsed: dict, model_label: str,
) -> str:
    pid = f"psx_{uuid.uuid4().hex[:12]}"
    with sqlite3.connect(ctx.db_path) as con:
        # One pending proposal per chapter — replace an unresolved one.
        con.execute(
            "UPDATE pending_state_extractions SET status='discarded', "
            "resolved_at=CURRENT_TIMESTAMP "
            "WHERE project_id=? AND chapter_id=? AND status='pending'",
            (ctx.project_id, ctx.chapter_id),
        )
        con.execute(
            "INSERT INTO pending_state_extractions "
            "(proposal_id, project_id, chapter_id, chapter_num, "
            " parsed_json, llm_model) VALUES (?, ?, ?, ?, ?, ?)",
            (pid, ctx.project_id, ctx.chapter_id, chapter_num,
             json.dumps(parsed, ensure_ascii=False), model_label),
        )
        con.commit()
    try:
        from ui.backend.app.services.notifications import create_notification
        create_notification(
            ctx.db_path,
            project_id=ctx.project_id,
            chapter_id=ctx.chapter_id,
            notification_type="state_extraction_pending",
            severity="medium",
            title="本章状态更新待确认",
            description=(
                "已从本章正文提取 Storyland 事实、标志性事件与伏笔/故事线"
                "节点标签，需确认后才会入库。"
            ),
            action_label="去确认",
            action_url=f"/chapters/{ctx.chapter_id}/review#pending",
        )
    except Exception as e:
        logger.debug("pending-extraction notification skipped: %s", e)
    return pid


async def run(ctx: "SubTaskContext") -> dict[str, Any]:
    chapter_text = _resolve_chapter_text(ctx)
    if not chapter_text.strip():
        return {"status": "skipped", "reason": "empty_chapter_text"}
    chapter_num = _resolve_chapter_num(ctx)
    existing = _list_existing_entity_names(ctx.db_path, ctx.project_id)
    existing_names = [e["name"] for e in existing]
    open_hooks = _list_open_hooks(ctx.db_path, ctx.project_id)
    active_subplots = _list_active_subplots(ctx.db_path, ctx.project_id)

    parsed, model_label = await _call_llm(
        chapter_text, chapter_num, existing_names,
        project_id=ctx.project_id, chapter_id=ctx.chapter_id,
        db_path=ctx.db_path,
        open_hooks=open_hooks, active_subplots=active_subplots,
    )

    # ── 确认 gate (Post-Commit·机制3): stash as proposal, apply later ──
    if _require_confirmation():
        proposal_id = _store_proposal(ctx, chapter_num, parsed, model_label)
        return {
            "status": "pending_confirmation",
            "proposal_id": proposal_id,
            "llm_model": model_label,
        }

    return _apply_parsed(ctx, chapter_num, parsed, model_label,
                         chapter_text=chapter_text,
                         open_hooks=open_hooks,
                         active_subplots=active_subplots)


def _apply_parsed(
    ctx: "SubTaskContext", chapter_num: int, parsed: dict, model_label: str,
    *, chapter_text: str = "",
    open_hooks: list[dict] | None = None,
    active_subplots: list[dict] | None = None,
) -> dict[str, Any]:
    """Persist every product of the merged extraction call. Shared by
    the direct-write path and the confirmation-gate apply endpoint."""
    if open_hooks is None:
        open_hooks = _list_open_hooks(ctx.db_path, ctx.project_id)
    if active_subplots is None:
        active_subplots = _list_active_subplots(ctx.db_path, ctx.project_id)
    if not chapter_text:
        chapter_text = _resolve_chapter_text(ctx)

    state_deltas    = parsed.get("state_deltas")    or []
    ledger_deltas   = parsed.get("ledger_deltas")   or []
    emotion_deltas  = parsed.get("emotion_deltas")  or []
    new_entities    = parsed.get("new_entities")    or []
    events_raw      = parsed.get("events")          or []
    hooks_raw       = parsed.get("hook_deltas")     or []
    subplots_raw    = parsed.get("subplot_updates") or []

    state_out = _apply_state_deltas(
        ctx.db_path, ctx.project_id, chapter_num, state_deltas, model_label,
    )
    ledger_out, ledger_warnings = _apply_ledger_deltas(
        ctx.db_path, ctx.project_id, chapter_num, ledger_deltas, model_label,
    )
    emotion_out = _apply_emotion_deltas(
        ctx.db_path, ctx.project_id, chapter_num, emotion_deltas,
    )
    entity_out = _resolve_entities(
        ctx.db_path, ctx.project_id, ctx.chapter_id, chapter_num,
        new_entities, chapter_text,
    )

    # ── 合并产物：读者记忆 L4 标志性事件 (Post-Commit·机制1) ──
    events = _normalize_events(events_raw)
    event_ids: list[str] = []
    if events:
        from .event_extractor import _persist as _persist_events
        event_ids = _persist_events(
            ctx.db_path, ctx.project_id, chapter_num, events, model_label,
        )

    # ── 合并产物：伏笔 / 故事线节点标签 (Post-Commit·机制1) ──
    hook_subplot_counts = _apply_hook_and_subplot_deltas(
        ctx.db_path, ctx.project_id, chapter_num,
        hooks_raw, subplots_raw,
        known_hook_ids={h["hook_id"] for h in open_hooks},
        known_thread_ids={s["thread_id"] for s in active_subplots},
    )

    # If any negative-balance warnings, create a medium-priority
    # notification so user can investigate.
    if ledger_warnings:
        try:
            from ui.backend.app.services.notifications import create_notification
            create_notification(
                ctx.db_path,
                project_id=ctx.project_id,
                chapter_id=ctx.chapter_id,
                notification_type="ledger_negative_balance",
                severity="medium",
                title="资源账本负数告警",
                description=(
                    f"本章自动提取的 ledger 变更导致 {len(ledger_warnings)} "
                    f"条账目出现负值：{'; '.join(ledger_warnings[:5])}"
                ),
                action_label="查看 ledger",
                action_url=f"/chapters/{ctx.chapter_id}/review#ledger",
            )
        except Exception as e:
            logger.debug("ledger warning notification skipped: %s", e)

    # Medium-priority "please review" nudge if anything was auto-extracted.
    total = (len(state_out) + len(ledger_out) + len(emotion_out)
             + len(entity_out["created"]) + len(entity_out["review_queue"])
             + len(event_ids) + hook_subplot_counts["hooks"]
             + hook_subplot_counts["subplots"])
    if total > 0:
        try:
            from ui.backend.app.services.notifications import create_notification
            create_notification(
                ctx.db_path,
                project_id=ctx.project_id,
                chapter_id=ctx.chapter_id,
                notification_type="state_changes_extracted",
                severity="medium",
                title="本章自动提取了状态变更",
                description=(
                    f"state×{len(state_out)} / ledger×{len(ledger_out)} / "
                    f"emotion×{len(emotion_out)} / 事件×{len(event_ids)} / "
                    f"伏笔节点×{hook_subplot_counts['hooks']} / "
                    f"故事线节点×{hook_subplot_counts['subplots']} / "
                    f"new entities×{len(entity_out['created'])} / "
                    f"pending review×{len(entity_out['review_queue'])}。建议审查。"
                ),
                action_label="审查变更",
                action_url=f"/chapters/{ctx.chapter_id}/review",
            )
        except Exception as e:
            logger.debug("state-extracted notification skipped: %s", e)

    return {
        "status":           "ok",
        "llm_model":        model_label,
        "state_changes":    len(state_out),
        "ledger_changes":   len(ledger_out),
        "emotion_changes":  len(emotion_out),
        "events":           len(event_ids),
        "hook_nodes":       hook_subplot_counts["hooks"],
        "subplot_nodes":    hook_subplot_counts["subplots"],
        "entities_created": len(entity_out["created"]),
        "entities_merged":  len(entity_out["merged"]),
        "entities_review":  len(entity_out["review_queue"]),
        "entities_discarded": len(entity_out["discarded"]),
        "ledger_warnings":  ledger_warnings,
    }
