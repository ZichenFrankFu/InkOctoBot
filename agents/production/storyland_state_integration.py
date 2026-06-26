"""
Writer × Truth Files integration (per docs/truth_file_system.md §9).

Three plug points exposed as standalone helpers — kept out of writer.py
so Writer stays focused on prose generation. The orchestration layer
(``/api/generation/single-writer`` endpoint, ``/api/generation/start``
pipeline) wires these in around the Writer call.

The three integration points from the roadmap doc:

1. **Phase 1 — prompt assembly** (``build_state_context``)
     Pulls the 7-file markdown bundle for the (chapter_num, characters)
     scope. Caller passes the result into Writer.write_chapter or
     Writer.assemble_chapter as ``memory_context``.

2. **Pressured-hook injection** (``list_pressured_hooks_text``)
     One-liner reminder of hooks that have gone N+ chapters without
     advancing. Appended to the Writer's narrative_instructions so the
     LLM knows what overdue threads to address.

3. **Phase 2 — settlement output** (``extract_and_apply_state_deltas``)
     After the Writer produces the chapter text, an LLM call extracts
     structured StorylandStateDeltas (StatePatches / HookDeltas /
     EmotionArcEntries / ChapterSummaryDelta) from
     the prose and applies via StorylandStateStore.apply_deltas (atomic,
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

from knowledge.storyland_state.schemas import (
    ChapterSummaryDelta,
    EmotionArcEntry,
    HookDelta,
    HookImportance,
    NumericalReconciliation,
    StatePatch,
    SubplotStatus,
    SubplotUpdate,
    StorylandStateDeltas,
    StorylandStateKind,
)
from knowledge.storyland_state.store import StorylandStateStore
from llm.base import LLMMessage

logger = logging.getLogger("inkoctobot.agents.production.storyland_state_integration")


# ─────────────── Phase 1: prompt assembly ──────────────────────────


def build_state_context(
    project_id: str,
    db_path: str,
    chapter_num: int,
    characters: list[str] | None = None,
    *,
    budgets_per_kind: int | None = None,
    pov_character: str | None = None,
) -> str:
    """Return the 7-file Truth bundle as a single Markdown blob.

    Caller injects this into Writer.write_chapter(memory_context=...)
    or Writer.assemble_chapter(memory_context=...). The bundle includes:
    current_state / particle_ledger / pending_hooks / chapter_summaries /
    subplot_board / emotional_arcs.

    A1 spoiler filter: pass ``pov_character`` for character-POV bundles
    (e.g. Actor Agent context) — spoiler hooks not yet revealed to that
    character are omitted. Pass ``None`` for omniscient narrator /
    single-writer view.

    Empty / nonexistent Truth files render as empty strings, so the
    bundle gracefully degrades on fresh projects.
    """
    try:
        store = StorylandStateStore(project_id=project_id, db_path=db_path)
        bundle = store.render_bundle_for_prompt(
            chapter_num=chapter_num,
            characters=characters or [],
            kinds=None,
            budgets=None if budgets_per_kind is None else {
                k: budgets_per_kind for k in StorylandStateKind
            },
            pov_character=pov_character,
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
        store = StorylandStateStore(project_id=project_id, db_path=db_path)
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


def _settlement_prompt() -> str:
    """Pull the settlement prompt from the registry so 设置 → 提示词 can
    override it. Lazy-imported to avoid registering at module-load time
    when the FastAPI app may not be available."""
    from reference_pipeline.prompts import render as _render_prompt
    return _render_prompt("pipeline.storyland_state_settlement")


def build_ledger_anchors(
    project_id: str,
    db_path: str,
    chapter_num: int,
    characters: list[str],
    *,
    common_keys: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Pre-read the latest ledger entries for the given characters.

    This is the **anchor** for InkOS PostWriteValidator (A2): we
    pre-emptively pull each character's recent ledger values so the
    Writer's settlement LLM call can be told the "old_value" it should
    write against. This makes ledger.closed_equation + ledger.matches_current
    validators meaningful.

    Without anchors, the LLM has to guess old_value or omit the
    reconciliation entirely → the audit gate can't catch numerical drift.

    Returns list of dicts: {character, key, current_value, last_chapter}.
    Empty list if no prior entries exist.
    """
    if not characters:
        return []
    try:
        store = StorylandStateStore(project_id=project_id, db_path=db_path)
        anchors: list[dict[str, Any]] = []
        for char in characters:
            entries = store.list_ledger_entries(character=char)
            # Group by key, keep the most recent entry per key
            latest_by_key: dict[str, dict] = {}
            for e in entries:
                k = e.get("key")
                if not k:
                    continue
                # Only consider entries before this chapter (= as_of N-1)
                if (e.get("chapter_num") or 0) >= chapter_num:
                    continue
                prev = latest_by_key.get(k)
                if (not prev) or (e.get("chapter_num") or 0) > (prev.get("chapter_num") or 0):
                    latest_by_key[k] = e
            for k, e in latest_by_key.items():
                anchors.append({
                    "character": char,
                    "key": k,
                    "category": e.get("category") or "resource",
                    "current_value": e.get("new_value"),
                    "last_chapter": e.get("chapter_num"),
                })
        return anchors
    except Exception as exc:
        logger.debug("anchor lookup skipped: %s", exc)
        return []


def _format_anchors_block(anchors: list[dict[str, Any]]) -> str:
    """Render anchor info into a markdown block for the settlement prompt."""
    if not anchors:
        return ""
    lines = [
        "[ledger 预存锚点 — 本章结算时必须以此为基准]",
        "（如果本章正文导致这些数值变化，必须在 particle_reconciliations 中输出，",
        " 且 old_value 必须等于此处的 current_value）",
    ]
    for a in anchors:
        lines.append(
            f"- {a['character']} · {a['category']}/{a['key']} = "
            f"{a['current_value']} (last set in 第{a['last_chapter']}章)"
        )
    return "\n".join(lines)


async def extract_state_deltas(
    router: Any,
    chapter_text: str,
    *,
    chapter_num: int,
    chapter_title: str = "",
    pov_character: str = "",
    agent_role: str = "consolidator",
    ledger_anchors: list[dict[str, Any]] | None = None,
) -> StorylandStateDeltas | None:
    """Run a settlement LLM call → StorylandStateDeltas.

    Pass ``ledger_anchors`` (from build_ledger_anchors) to enable the
    InkOS PostWriteValidator pattern — the prompt will surface them as
    "must match old_value" pre-conditions.

    Returns None if parsing fails. Caller handles None gracefully.
    """
    meta_lines: list[str] = []
    if chapter_title:
        meta_lines.append(f"章节标题：{chapter_title}")
    if pov_character:
        meta_lines.append(f"POV 视角：{pov_character}")
    meta = "\n".join(meta_lines)

    anchor_block = _format_anchors_block(ledger_anchors or [])

    user_content = (
        f"第 {chapter_num} 章正文：\n\n{chapter_text}\n\n"
        f"{('章节元数据：' + chr(10) + meta + chr(10) + chr(10)) if meta else ''}"
        f"{(anchor_block + chr(10) + chr(10)) if anchor_block else ''}"
        "请按上述 schema 抽取本章的结构化变化。"
    )

    settlement_system = _settlement_prompt()
    messages = [
        LLMMessage(role="system", content=settlement_system),
        LLMMessage(role="user", content=user_content),
    ]

    # Settlement LLM call goes through LLMCallSite so it shows up in
    # the unified llm_outputs audit (call_site_id =
    # storyland_state.settlement) and supports the global manual-mode
    # paste toggle just like every other LLM call. The agent's own
    # router is still used inside the auto_executor closure so callers'
    # router-injection patterns (and test mocks of ``router.generate``)
    # keep working.
    captured: dict[str, Any] = {}

    async def _auto_executor() -> str:
        resp = await router.generate(
            agent_role=agent_role, messages=messages,
            temperature=0.2, max_tokens=4000,
        )
        captured["resp"] = resp
        return resp.content or ""

    try:
        from llm.call_site import with_audit_and_manual_mode
        raw_response = await with_audit_and_manual_mode(
            call_site_id="storyland_state.settlement",
            primary_role=agent_role,
            prompt_full=user_content,
            system_prompt=settlement_system,
            auto_executor=_auto_executor,
            parsed_target_table="truth_current_state",
        )
    except Exception as e:
        logger.warning("settlement LLM call failed: %s", e)
        return None

    parsed = _parse_settlement_response(raw_response)
    if parsed is None:
        logger.warning(
            "settlement parse failed for chapter=%d; raw_head=%r",
            chapter_num, (raw_response or "")[:300],
        )
        return None

    try:
        return _build_state_deltas(parsed, chapter_num)
    except Exception as e:
        logger.warning("settlement → StorylandStateDeltas conversion failed: %s", e)
        return None


def apply_state_deltas(
    deltas: StorylandStateDeltas,
    *,
    project_id: str,
    db_path: str,
    known_characters: set[str] | None = None,
):
    """Apply via StorylandStateStore.apply_deltas. Returns ApplyResult.

    The return value is the **audit gate**: ``cross_ref_issues`` with
    ``severity == "error"`` indicates the chapter should NOT be marked
    finalized; ``severity == "warning"`` / ``"info"`` is advisory.
    """
    store = StorylandStateStore(project_id=project_id, db_path=db_path)
    return store.apply_deltas(
        deltas,
        validate=True,
        known_characters=known_characters,
        allow_backfill=False,
    )


async def extract_and_apply_state_deltas(
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
    ledger_anchors: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Convenience: settlement + apply in one call.

    Returns flat dict for HTTP serialization with these fields:
      success: bool — false on parse failure, apply error, OR any
               error-severity cross_ref_issues (the **audit gate**)
      audit_status: 'audit_passed' / 'audit_failed' / 'audit_pending'
      applied_counts: per-table row counts
      issues: CoT-formatted concise audit summary (errors first)
      cross_ref_issues: raw issue dicts (for debug surface)
      has_errors: bool — true if any error-severity issue
      deltas_hash: idempotency key
      idempotent_hit: bool
      error: parse/apply exception text, or None
    """
    deltas = await extract_state_deltas(
        router, chapter_text,
        chapter_num=chapter_num,
        chapter_title=chapter_title,
        pov_character=pov_character,
        agent_role=agent_role,
        ledger_anchors=ledger_anchors,
    )
    if deltas is None:
        issues = [{
            "rule_id": "_meta", "severity": "error",
            "title": "结算失败",
            "thought": "LLM 返回的内容不是有效 JSON",
            "observation": "settlement extraction returned unparseable output",
            "suggestion": "检查模型温度 / 上下文长度 / prompt 是否被截断",
        }]
        return {
            "success": False, "audit_status": "audit_failed",
            "applied_counts": {}, "cross_ref_issues": [],
            "issues": issues, "has_errors": True,
            "deltas_hash": "", "idempotent_hit": False,
            "error": "settlement extraction failed (LLM returned unparseable output)",
        }
    try:
        result = apply_state_deltas(
            deltas,
            project_id=project_id,
            db_path=db_path,
            known_characters=known_characters,
        )
        raw_issues = [
            {"rule_id": i.rule_id, "severity": i.severity,
             "message": i.message, "location": i.location}
            for i in result.cross_ref_issues
        ]
        issues = summarize_audit_issues(raw_issues, result.applied_counts)
        has_errors = any(i["severity"] == "error" for i in issues if i.get("rule_id") != "_meta")
        audit_status = "audit_failed" if has_errors else "audit_passed"
        return {
            "success": result.success and not has_errors,
            "audit_status": audit_status,
            "applied_counts": result.applied_counts,
            "cross_ref_issues": raw_issues,
            "issues": issues,
            "has_errors": has_errors,
            "deltas_hash": result.deltas_hash,
            "idempotent_hit": result.idempotent_hit,
            "error": None,
        }
    except Exception as e:
        logger.exception("apply_deltas failed")
        return {
            "success": False, "audit_status": "audit_failed",
            "applied_counts": {}, "cross_ref_issues": [],
            "issues": [{
                "rule_id": "_meta", "severity": "error", "title": "落库失败",
                "thought": "apply_deltas SQL 事务失败",
                "observation": f"{type(e).__name__}: {str(e)[:200]}",
                "suggestion": "查 storage/connection 日志看具体 SQL 错误",
            }],
            "has_errors": True,
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


def _build_state_deltas(parsed: dict[str, Any], chapter_num: int) -> StorylandStateDeltas:
    """Convert the LLM's JSON to a StorylandStateDeltas object.

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

    # Particle reconciliations (A2 — ledger anchors)
    particle_reconciliations: list[NumericalReconciliation] = []
    for nr in parsed.get("particle_reconciliations") or []:
        if not isinstance(nr, dict):
            continue
        char = (nr.get("character") or "").strip()
        resource = (nr.get("resource") or nr.get("key") or "").strip()
        if not char or not resource:
            continue
        op = nr.get("operation") or "subtract"
        if op not in {"add", "subtract", "set"}:
            op = "subtract"
        try:
            old_v = int(nr.get("old_value") or 0)
            delta = int(nr.get("delta") or 0)
            new_v = int(nr.get("new_value") or 0)
        except (TypeError, ValueError):
            continue
        reason = (nr.get("reason") or "").strip() or "未注明"
        evidence = (nr.get("in_text_evidence") or "")[:80]
        if not evidence:
            evidence = "（无引用）"
        particle_reconciliations.append(NumericalReconciliation(
            character=char, resource=resource,
            old_value=old_v, operation=op, delta=delta, new_value=new_v,
            reason=reason, in_text_evidence=evidence,
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

    # Subplot updates (A4)
    subplot_updates: list[SubplotUpdate] = []
    for su in parsed.get("subplot_updates") or []:
        if not isinstance(su, dict):
            continue
        name = (su.get("name") or "").strip()
        if not name:
            continue
        action = su.get("action") or "advance"
        if action not in {"new", "advance", "climax", "resolve", "dormant"}:
            action = "advance"
        status_raw = (su.get("status_after") or "building").lower()
        try:
            status_after = SubplotStatus(status_raw)
        except ValueError:
            status_after = SubplotStatus.building
        related = su.get("related_hook_ids") or []
        if not isinstance(related, list):
            related = []
        subplot_updates.append(SubplotUpdate(
            thread_id=su.get("thread_id"),
            name=name, action=action, status_after=status_after,
            related_hook_ids=[str(x) for x in related if x],
            note=su.get("note") or "",
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

    return StorylandStateDeltas(
        chapter_num=chapter_num,
        current_state_patches=state_patches,
        particle_reconciliations=particle_reconciliations,
        hook_deltas=hook_deltas,
        chapter_summary=chapter_summary,
        subplot_updates=subplot_updates,
        emotion_arc_entries=emotion_entries,
    )


# ─────────────── B2: Audit gate — concise CoT-style summary ────────


def summarize_audit_issues(
    cross_ref_issues: list[dict[str, Any]],
    applied_counts: dict[str, int] | None = None,
) -> list[dict[str, Any]]:
    """Turn ApplyResult.cross_ref_issues into CoT-style concise bullets.

    Each output entry has:
      severity:   'error' / 'warning' / 'info'
      title:      4-8 char Chinese label (rule type, not message)
      thought:    1-line "I noticed X" (CoT step)
      observation: 1-line specific evidence
      suggestion: 1-line fix hint

    Keeps the audit panel readable: instead of dumping a Pydantic
    ValidationIssue blob, the user sees a chain-of-thought-flavored
    explanation. Errors are listed first.
    """
    rule_titles: dict[str, tuple[str, str]] = {
        # Layer 1 (within-delta)
        "hook.no_duplicate_id":      ("伏笔重复",   "同一次提交里出现了相同的 hook_id"),
        "ledger.closed_equation":    ("账本未闭合", "old + delta ≠ new"),
        "state.no_conflicting_triple": ("状态冲突", "同一主谓上同时 upsert 和 invalidate"),
        # Layer 2 (xref)
        "xref.character_in_emotion": ("情绪不识人", "情绪条目里的角色不在已知列表"),
        "xref.character_in_relation":("关系不识人", "关系条目里的角色不在已知列表"),
        # Layer 3 (DB)
        "chapter.monotonic":         ("章节倒退",   "本次结算的章节号不大于已结算的最高"),
        "hook.no_orphan_progress":   ("伏笔无源",   "推进/回收/放弃的伏笔不存在"),
        "hook.transition_valid":     ("终态再变",   "试图修改已 resolved/abandoned 的伏笔"),
        "ledger.matches_current":    ("锚点不符",   "old_value 与 DB 实际值不一致"),
        "xref.hook_in_subplot":      ("副线缺伏笔", "副线引用的 hook_id 不存在"),
        "relation.symmetric_sentiment_drift": ("关系不对称", "A→B 与 B→A 的情感分差过大"),
        "subplot.hook_resolved_subplot_should_advance":
            ("副线滞后",   "伏笔回收了但相关副线仍在 setup"),
        "audit.orphaned_subplot_hooks": ("副线孤儿", "副线引用了已删除的伏笔"),
        "audit.emotion_arc_unknown_character":
            ("情绪幽灵", "情绪轨迹里的角色不在角色卡里"),
    }
    severity_order = {"error": 0, "warning": 1, "info": 2}

    out: list[dict[str, Any]] = []
    for issue in cross_ref_issues or []:
        rule = (issue.get("rule_id") or "")
        severity = (issue.get("severity") or "info").lower()
        message = (issue.get("message") or "").strip()
        location = (issue.get("location") or "").strip()

        title, thought = rule_titles.get(rule, (rule.split(".")[-1][:8] or "未知", "事实库审计发现问题"))

        # Concise observation — pull the most specific 1-line evidence
        observation = message if message else "（无具体信息）"
        if len(observation) > 100:
            observation = observation[:97] + "…"
        if location:
            observation = f"{observation} （位置：{location}）"

        suggestion = _rule_suggestion(rule, severity)

        out.append({
            "rule_id": rule,
            "severity": severity,
            "title": title,
            "thought": thought,
            "observation": observation,
            "suggestion": suggestion,
        })

    # Errors first, then warnings, then info
    out.sort(key=lambda x: severity_order.get(x["severity"], 9))

    # Append a single-line summary header for the UI
    if applied_counts:
        applied_summary = "、".join(
            f"{k}×{v}" for k, v in applied_counts.items() if v
        )
        if applied_summary:
            out.insert(0, {
                "rule_id": "_meta", "severity": "info",
                "title": "已写入",
                "thought": "事实库本次成功落库的条目（在所有 audit 通过后）",
                "observation": applied_summary,
                "suggestion": "",
            })
    return out


def _rule_suggestion(rule: str, severity: str) -> str:
    """Per-rule suggestion text. Falls back to generic guidance."""
    sug: dict[str, str] = {
        "ledger.closed_equation":
            "检查 LLM 输出的 delta / old_value / new_value 是否满足 old + delta == new",
        "ledger.matches_current":
            "Writer 接收到的锚点与 DB 不一致 — 大概率是 LLM 凭空写了 old_value",
        "chapter.monotonic":
            "结算时的 chapter_num 必须大于已结算的最大值 — 检查 chapter_num 入参",
        "hook.no_orphan_progress":
            "Writer 想推进一个不存在的伏笔 — 让 Writer 先用 action='new' 创建",
        "hook.transition_valid":
            "已 resolved 的伏笔不能再变 — 如果确实要新动作，开新伏笔",
        "xref.character_in_emotion":
            "情绪条目里的角色名拼写错误，或还没在 characters 表注册",
        "xref.character_in_relation":
            "关系条目里的角色名拼写错误，或还没在 characters 表注册",
        "subplot.hook_resolved_subplot_should_advance":
            "建议同时给相关 subplot 发一个 advance/climax 更新",
    }
    if rule in sug:
        return sug[rule]
    if severity == "error":
        return "需要修复后才能 finalize 本章"
    return "可忽略，但建议检查"
