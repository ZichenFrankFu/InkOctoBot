"""preference_analyzer — 用户偏好批量学习 (spec 4.1 + EditAnalyzer·机制1/2).

Runs on every commit but only does work every N committed chapters
(default 5, settings key ``preference_learning_interval``) — spec
用户偏好·机制2: 批量学习一次，不每章触发.

Per batch it:
1. collects each covered chapter's first AI draft vs latest version
   from ``text_versions`` (用户偏好·机制1: 对比草稿与定稿提取差异);
2. collects those chapters' ``special_requirements``, which carry
   higher weight than edit-inferred signals (用户偏好·机制3);
3. makes ONE LLM call (through LLMCallSite, so manual mode / tokenizer
   / audit apply) that returns style / content / pacing preferences,
   each tagged with its source chapters;
4. writes them into ``user_style_preferences`` as ``is_confirmed=0``
   pending rows — they are NOT injected into prompts until the user
   confirms (用户偏好·机制4 + LLM交互·机制2);
5. records the batch in ``preference_learning_runs`` and raises a
   notification pointing the user at the review surface.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from typing import TYPE_CHECKING, Any

from .summarizer import _extract_json

logger = logging.getLogger("inkoctobot.commit_pipeline.preference_analyzer")

if TYPE_CHECKING:
    from . import SubTaskContext


DEFAULT_INTERVAL = 5
_MAX_CHAPTERS_PER_BATCH = 8     # prompt size guard
_MAX_TEXT_PER_SIDE = 2200       # chars per draft/final excerpt

_SYSTEM = (
    "你是写作偏好分析助手。对比同一章节的 AI 初稿与用户定稿，"
    "并结合用户的本章特殊要求，归纳用户的写作偏好；同时识别这批章节"
    "涉及的专业领域。\n"
    "要求：\n"
    "1. 偏好分三类：style（风格）、content（内容）、pacing（节奏）\n"
    "2. 每条偏好为一句可执行的描述（如「对话多用短句，少用书面语」），"
    "不要复述原文\n"
    "3. 特殊要求中体现的偏好权重高于从修改差异推断的偏好\n"
    "4. 只总结有明确证据的偏好，没有就返回空数组\n"
    "5. professional_domains：列出正文明显涉及、写错会被读者识破的"
    "专业领域（如 中医、法医、航海、刑侦），最多 3 个，"
    "没有就返回空数组\n"
    "6. 输出 JSON：{\"preferences\": [{\"type\": \"style|content|pacing\", "
    "\"description\": \"偏好描述\", \"source_chapters\": [章节号], "
    "\"source_type\": \"edit_inference|special_requirements\"}], "
    "\"professional_domains\": [{\"domain\": \"领域名\", "
    "\"reason\": \"正文涉及点\"}]}"
)


def _interval() -> int:
    """N from data/settings.json (用户可调, default 5)."""
    try:
        from ui.backend.app.settings import settings as _s
        path = _s.get_data_path("settings.json")
        if path.exists():
            blob = json.loads(path.read_text("utf-8"))
            n = int(blob.get("preference_learning_interval") or 0)
            if n > 0:
                return n
    except Exception:
        pass
    return DEFAULT_INTERVAL


def _committed_chapters(con: sqlite3.Connection, project_id: str) -> list[dict]:
    """Chapters with at least one text version, in chapter order."""
    rows = con.execute(
        """SELECT c.chapter_id, c.chapter_num,
                  COALESCE(c.special_requirements, '') AS special_requirements
           FROM chapters c
           WHERE c.project_id = ?
             AND EXISTS (SELECT 1 FROM text_versions v
                         WHERE v.chapter_id = c.chapter_id)
           ORDER BY c.chapter_num""",
        (project_id,),
    ).fetchall()
    return [
        {"chapter_id": r[0], "chapter_num": r[1], "special_requirements": r[2]}
        for r in rows
    ]


def _last_run_marker(con: sqlite3.Connection, project_id: str) -> int:
    row = con.execute(
        "SELECT MAX(chapter_count_at_run) FROM preference_learning_runs "
        "WHERE project_id = ?",
        (project_id,),
    ).fetchone()
    return int(row[0]) if row and row[0] else 0


def _draft_final_pair(
    con: sqlite3.Connection, chapter_id: str,
) -> tuple[str, str]:
    """(first AI draft, latest version). Either may be ''."""
    draft = con.execute(
        "SELECT content FROM text_versions "
        "WHERE chapter_id = ? AND source = 'ai' "
        "ORDER BY version_num ASC LIMIT 1",
        (chapter_id,),
    ).fetchone()
    final = con.execute(
        "SELECT content FROM text_versions "
        "WHERE chapter_id = ? ORDER BY version_num DESC LIMIT 1",
        (chapter_id,),
    ).fetchone()
    return (draft[0] if draft else "", final[0] if final else "")


async def _call_llm(prompt: str, ctx: "SubTaskContext") -> dict:
    """One LLM call for the whole batch — routed through LLMCallSite so
    auto/manual mode, tokenizer and the audit row apply uniformly.
    Module-level so tests can stub it (mirrors the other sub-tasks)."""
    from llm.call_site import LLMCallSite
    cs = LLMCallSite(
        call_site_id="post_commit.preference_analyzer",
        primary_role="post_commit",
        parsed_target_table="user_style_preferences",
        default_max_tokens=1500, default_temperature=0.3,
    )
    raw = await cs.invoke(
        prompt=prompt,
        system=_SYSTEM,
        project_id=ctx.project_id, chapter_id=ctx.chapter_id,
        db_path=ctx.db_path,
    )
    return _extract_json(raw)


def _build_prompt(batch: list[dict]) -> str:
    parts: list[str] = []
    for item in batch:
        ch = item["chapter_num"]
        parts.append(f"## 第 {ch} 章")
        if item["special_requirements"]:
            parts.append(f"[本章特殊要求]\n{item['special_requirements'][:600]}")
        if item["has_diff"]:
            parts.append(f"[AI 初稿（节选）]\n{item['draft'][:_MAX_TEXT_PER_SIDE]}")
            parts.append(f"[用户定稿（节选）]\n{item['final'][:_MAX_TEXT_PER_SIDE]}")
        else:
            parts.append("[本章用户未修改 AI 稿]")
        parts.append("")
    parts.append("请输出 JSON。")
    return "\n".join(parts)


def _upsert_preferences(
    con: sqlite3.Connection, project_id: str, prefs: list[dict],
) -> int:
    """Insert / merge preferences as PENDING (is_confirmed=0) rows.

    A repeated description bumps observation_count + confidence and
    merges source chapters; it never auto-confirms (机制4).
    """
    written = 0
    for p in prefs:
        ptype = str(p.get("type") or "").strip()
        desc = str(p.get("description") or "").strip()
        if ptype not in ("style", "content", "pacing") or not desc:
            continue
        src_type = str(p.get("source_type") or "edit_inference").strip()
        if src_type not in ("edit_inference", "special_requirements"):
            src_type = "edit_inference"
        chapters = [
            int(c) for c in (p.get("source_chapters") or [])
            if isinstance(c, (int, float, str)) and str(c).strip().isdigit()
        ]
        existing = con.execute(
            "SELECT pref_id, observation_count, source_chapters_json "
            "FROM user_style_preferences "
            "WHERE project_id=? AND preference_type=? AND description=?",
            (project_id, ptype, desc),
        ).fetchone()
        # 特殊要求来源置信度起点更高 (机制3)
        base_conf = 0.4 if src_type == "special_requirements" else 0.2
        if existing:
            count = int(existing[1]) + 1
            try:
                merged = sorted(set(json.loads(existing[2] or "[]")) | set(chapters))
            except Exception:
                merged = chapters
            con.execute(
                "UPDATE user_style_preferences SET observation_count=?, "
                "confidence=?, source_chapters_json=?, source_type=?, "
                "updated_at=CURRENT_TIMESTAMP WHERE pref_id=?",
                (count, min(1.0, base_conf + count * 0.15),
                 json.dumps(merged), src_type, existing[0]),
            )
        else:
            con.execute(
                "INSERT INTO user_style_preferences "
                "(pref_id, project_id, preference_type, description, "
                " confidence, observation_count, is_confirmed, "
                " source_chapters_json, source_type) "
                "VALUES (?, ?, ?, ?, ?, 1, 0, ?, ?)",
                (f"prf_{uuid.uuid4().hex[:12]}", project_id, ptype, desc,
                 base_conf, json.dumps(sorted(set(chapters))), src_type),
            )
        written += 1
    return written


async def run(ctx: "SubTaskContext") -> dict[str, Any]:
    interval = _interval()
    with sqlite3.connect(ctx.db_path) as con:
        chapters = _committed_chapters(con, ctx.project_id)
        marker = _last_run_marker(con, ctx.project_id)
    total = len(chapters)
    if total - marker < interval:
        return {
            "skipped": "interval_not_reached",
            "committed_chapters": total,
            "last_run_at_count": marker,
            "interval": interval,
        }

    # Chapters committed since the last run (cap for prompt size).
    new_chapters = chapters[marker:][-_MAX_CHAPTERS_PER_BATCH:]
    batch: list[dict] = []
    with sqlite3.connect(ctx.db_path) as con:
        for ch in new_chapters:
            draft, final = _draft_final_pair(con, ch["chapter_id"])
            has_diff = bool(draft and final and draft.strip() != final.strip())
            if not has_diff and not ch["special_requirements"]:
                continue
            batch.append({
                **ch, "draft": draft, "final": final, "has_diff": has_diff,
            })
    if not batch:
        # Still advance the marker so we don't re-scan the same empty
        # window forever.
        _record_run(ctx, total, [], 0)
        return {"skipped": "no_edit_signal", "committed_chapters": total}

    parsed = await _call_llm(_build_prompt(batch), ctx)
    prefs = parsed.get("preferences") or []
    domains = parsed.get("professional_domains") or []

    with sqlite3.connect(ctx.db_path) as con:
        written = _upsert_preferences(con, ctx.project_id, prefs)
        con.commit()
    covered = [c["chapter_num"] for c in batch]
    _record_run(ctx, total, covered, written)

    # 专业知识·机制1 + Post-Commit·机制4: 检测到专业领域 → 询问用户
    # 是否触发联网研究（仅通知，研究由用户在自学习页主动确认发起）。
    domains_notified = _notify_domains(ctx, domains, covered)

    if written:
        try:
            from ui.backend.app.services.notifications.service import (
                create_notification,
            )
            create_notification(
                ctx.db_path,
                project_id=ctx.project_id,
                notification_type="preference_learning",
                severity="medium",
                title=f"学习到 {written} 条用户偏好，等待确认",
                description=(
                    f"对第 {covered[0]}-{covered[-1]} 章的修改差异与特殊要求"
                    "进行了批量偏好学习。确认后才会注入后续章节生成。"
                ),
                action_label="去确认",
                action_url="/skills?tab=preferences",
            )
        except Exception as e:
            logger.debug("preference notification skipped: %s", e)

    return {
        "analyzed_chapters": covered,
        "prefs_emitted": written,
        "domains_detected": domains_notified,
        "interval": interval,
    }


def _notify_domains(
    ctx: "SubTaskContext", domains: Any, covered: list[int],
) -> list[str]:
    """One notification per detected professional domain, asking the
    user whether to research it (机制1: 由用户判断该领域准确性是否
    重要，确认后才发起联网研究)."""
    names: list[str] = []
    if not isinstance(domains, list):
        return names
    try:
        from ui.backend.app.services.notifications.service import (
            create_notification,
        )
    except Exception:
        return names
    for d in domains[:3]:
        if not isinstance(d, dict):
            continue
        domain = str(d.get("domain") or "").strip()
        if not domain:
            continue
        reason = str(d.get("reason") or "").strip()
        try:
            create_notification(
                ctx.db_path,
                project_id=ctx.project_id,
                notification_type="knowledge_research_suggested",
                severity="low",
                title=f"是否补充「{domain}」领域的专业知识？",
                description=(
                    f"第 {covered[0]}-{covered[-1]} 章涉及该领域"
                    f"（{reason or '正文出现相关内容'}）。"
                    "确认后将联网研究并编译为知识 skill，"
                    "生成时按章节相关度自动注入。"
                ),
                action_label="去研究",
                action_url=f"/skills?tab=knowledge&domain={domain}",
            )
            names.append(domain)
        except Exception as e:
            logger.debug("domain notification skipped: %s", e)
    return names


def _record_run(
    ctx: "SubTaskContext", chapter_count: int,
    covered: list[int], written: int,
) -> None:
    with sqlite3.connect(ctx.db_path) as con:
        con.execute(
            "INSERT INTO preference_learning_runs "
            "(run_id, project_id, chapters_covered_json, "
            " chapter_count_at_run, prefs_emitted) VALUES (?, ?, ?, ?, ?)",
            (f"plr_{uuid.uuid4().hex[:12]}", ctx.project_id,
             json.dumps(covered), chapter_count, written),
        )
        con.commit()
