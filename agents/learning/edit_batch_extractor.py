"""Batch extraction of writing preferences from accumulated edits.

When :func:`fire_batch_extraction` is invoked, we:

1. Pull all unconsumed observations for the project.
2. Build a single prompt that prioritises each chapter's
   ``special_requirement`` (explicit user intent, high-weight) over
   the diff-derived edit signals (implicit, lower-weight).
3. Run one LLM call (``edit_learning.batch_extract``) asking for two
   things in one JSON: distilled preferences AND a domain-gap probe
   (so Part B gets its candidate domains for free).
4. Persist the preferences with weighted starting confidence
   (``source='special_req'`` → 0.4, ``source='edit'`` → 0.15), bump
   ``observation_count`` + confidence on repeats.
5. Mark the consumed observations.
6. Insert any new domain candidates into ``domain_suggestions`` for
   Part B's gate-1 UI.

The whole pipeline is fire-and-forget and best-effort: a failure
leaves observations marked ``consumed=0`` so the next batch retries
naturally."""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
import uuid
from typing import Any

logger = logging.getLogger("inkoctobot.agents.learning.edit_batch_extractor")


_SPECIAL_REQ_BASE_CONFIDENCE = 0.4
_EDIT_BASE_CONFIDENCE = 0.15
_PREF_CONFIDENCE_STEP = 0.15
_PREF_CONFIDENCE_CAP = 1.0

_DIFF_SAMPLE_HEAD = 600
_DIFF_SAMPLE_TAIL = 600


def _open(db_path: str) -> sqlite3.Connection:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    return con


# ─────────── observation fetch / mark ───────────


def _fetch_unconsumed(con: sqlite3.Connection, project_id: str) -> list[dict]:
    rows = con.execute(
        """SELECT obs_id, chapter_id, chapter_num,
                  original_text, edited_text, special_requirement,
                  diff_stats_json
           FROM edit_observations
           WHERE project_id = ? AND consumed = 0
           ORDER BY created_at""",
        (project_id,),
    ).fetchall()
    out: list[dict] = []
    for r in rows:
        try:
            stats = json.loads(r["diff_stats_json"] or "{}")
        except Exception:
            stats = {}
        out.append({
            "obs_id":              r["obs_id"],
            "chapter_id":          r["chapter_id"],
            "chapter_num":         int(r["chapter_num"] or 0),
            "original_text":       r["original_text"] or "",
            "edited_text":         r["edited_text"] or "",
            "special_requirement": r["special_requirement"] or "",
            "diff_stats":          stats,
        })
    return out


def _mark_consumed(con: sqlite3.Connection, obs_ids: list[str]) -> None:
    if not obs_ids:
        return
    placeholders = ",".join("?" for _ in obs_ids)
    con.execute(
        f"UPDATE edit_observations SET consumed = 1 "
        f"WHERE obs_id IN ({placeholders})",
        obs_ids,
    )


# ─────────── prompt builder ───────────


def _sample_text(text: str) -> str:
    """Return a head+tail sample so the prompt sees both sides without
    paying for the entire chapter when chapters are long."""
    text = text or ""
    if len(text) <= _DIFF_SAMPLE_HEAD + _DIFF_SAMPLE_TAIL + 32:
        return text
    return (
        text[:_DIFF_SAMPLE_HEAD]
        + f"\n...（中段省略 {len(text) - _DIFF_SAMPLE_HEAD - _DIFF_SAMPLE_TAIL} 字）...\n"
        + text[-_DIFF_SAMPLE_TAIL:]
    )


_SYSTEM_PROMPT = (
    "你是网文创作偏好学习助手。给你一组章节，每章含作者的"
    "【特别要求】(显式高权重信号) 与【AI生成 vs 用户修改】的差异采样 "
    "(隐式信号)。请总结作者的稳定写作偏好，并判断作者所写题材是否"
    "需要补充某个专业领域的知识来支撑创作的专业质感。"
    "\n只输出 JSON，禁止任何额外文字。"
)


_USER_PROMPT_HEADER = (
    "请基于以下数据输出 JSON，结构必须为：\n"
    "{\n"
    '  "style_preferences":   [{"description": str, "source": "special_req"|"edit"}],\n'
    '  "content_preferences": [{"description": str, "source": "special_req"|"edit"}],\n'
    '  "pacing_preferences":  [{"description": str, "source": "special_req"|"edit"}],\n'
    '  "domain_gap": {\n'
    '     "needs_domain_knowledge": bool,\n'
    '     "candidate_domains": [{"domain": str, "sub_domain": str, "rationale": str}]\n'
    "  }\n"
    "}\n\n"
    "权重提示：来自【特别要求】的偏好用 source=\"special_req\"；"
    "来自编辑差异的偏好用 source=\"edit\"。\n"
)


def _build_prompt(observations: list[dict]) -> str:
    parts: list[str] = [_USER_PROMPT_HEADER, "数据:"]
    for i, obs in enumerate(observations, 1):
        parts.append(
            f"\n## 章节 {obs['chapter_num']} (obs {i})"
        )
        sr = obs["special_requirement"].strip()
        if sr:
            parts.append(f"【特别要求 (高优先)】\n{sr}")
        else:
            parts.append("【特别要求】无")
        stats = obs["diff_stats"]
        parts.append(f"【diff 统计】{json.dumps(stats, ensure_ascii=False)}")
        parts.append(
            "【AI 原版（采样）】\n" + _sample_text(obs["original_text"])
        )
        parts.append(
            "【用户修改后（采样）】\n" + _sample_text(obs["edited_text"])
        )
    return "\n".join(parts)


# ─────────── extraction ───────────


async def extract_batch(
    *,
    db_path: str, project_id: str,
    observations: list[dict] | None = None,
) -> dict[str, Any]:
    """Run the LLM extraction over ``observations`` (or fetch them).

    Returns the parsed JSON (preferences + domain_gap). Persistence
    happens in :func:`_persist`. Raises on LLM/parse failure so the
    caller can leave ``consumed=0`` and retry next time."""
    if observations is None:
        with _open(db_path) as con:
            observations = _fetch_unconsumed(con, project_id)
    if not observations:
        return {}

    from llm.call_site import LLMCallSite
    cs = LLMCallSite(
        call_site_id="edit_learning.batch_extract",
        primary_role="post_commit",
        parsed_target_table="user_style_preferences",
        default_max_tokens=2000,
        default_temperature=0.3,
    )
    raw = await cs.invoke(
        prompt=_build_prompt(observations),
        system=_SYSTEM_PROMPT,
        project_id=project_id,
        db_path=db_path,
    )
    return _parse_response(raw)


_JSON_FENCE_RE = None


def _parse_response(raw: str) -> dict[str, Any]:
    """Tolerant JSON extraction — strips fences, trims, parses."""
    if not raw:
        return {}
    text = raw.strip()
    # Strip ```json ... ``` if present.
    if text.startswith("```"):
        text = text.lstrip("`")
        if text.lower().startswith("json"):
            text = text[4:]
        text = text.strip()
        if text.endswith("```"):
            text = text[:-3].strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Fall back to first {...} substring.
        i = text.find("{")
        j = text.rfind("}")
        if i >= 0 and j > i:
            try:
                return json.loads(text[i:j + 1])
            except json.JSONDecodeError:
                pass
    logger.warning("batch_extract response not parseable: %r", raw[:200])
    return {}


# ─────────── persistence ───────────


def _persist_preferences(
    con: sqlite3.Connection, project_id: str, parsed: dict[str, Any],
) -> int:
    """Write learned preferences. ``source='special_req'`` rows get a
    higher starting confidence than ``edit`` rows. Returns count
    of preferences inserted/updated."""
    n = 0
    for ptype, key in (
        ("style",   "style_preferences"),
        ("content", "content_preferences"),
        ("pacing",  "pacing_preferences"),
    ):
        for item in (parsed.get(key) or []):
            desc = (item.get("description") or "").strip() \
                if isinstance(item, dict) else str(item or "").strip()
            if not desc:
                continue
            src = (
                item.get("source", "edit").strip().lower()
                if isinstance(item, dict) else "edit"
            )
            base = _SPECIAL_REQ_BASE_CONFIDENCE if src == "special_req" \
                else _EDIT_BASE_CONFIDENCE

            existing = con.execute(
                """SELECT pref_id, observation_count, confidence
                   FROM user_style_preferences
                   WHERE project_id=? AND preference_type=? AND description=?""",
                (project_id, ptype, desc),
            ).fetchone()
            if existing:
                new_count = int(existing["observation_count"] or 0) + 1
                new_conf = min(
                    _PREF_CONFIDENCE_CAP,
                    float(existing["confidence"] or 0.0) + _PREF_CONFIDENCE_STEP,
                )
                con.execute(
                    """UPDATE user_style_preferences
                       SET observation_count=?, confidence=?,
                           updated_at=CURRENT_TIMESTAMP
                       WHERE pref_id=?""",
                    (new_count, new_conf, existing["pref_id"]),
                )
            else:
                con.execute(
                    """INSERT INTO user_style_preferences
                       (pref_id, project_id, preference_type, description,
                        confidence, observation_count)
                       VALUES (?, ?, ?, ?, ?, 1)""",
                    (f"prf_{uuid.uuid4().hex[:12]}",
                     project_id, ptype, desc, base),
                )
            n += 1
    return n


def _persist_domain_gap(
    con: sqlite3.Connection, project_id: str, parsed: dict[str, Any],
) -> int:
    """Insert ``proposed`` rows for each NEW candidate domain.

    Existing rows (any status) suppress re-suggesting the same domain —
    once the user rejected ``astrophysics`` we don't keep nagging."""
    gap = parsed.get("domain_gap") or {}
    if not gap.get("needs_domain_knowledge"):
        return 0
    candidates = gap.get("candidate_domains") or []
    if not isinstance(candidates, list):
        return 0

    n = 0
    now = time.time()
    for c in candidates:
        if not isinstance(c, dict):
            continue
        domain = (c.get("domain") or "").strip()
        if not domain:
            continue
        sub_domain = (c.get("sub_domain") or "").strip()
        rationale = (c.get("rationale") or "").strip()

        # Suppress duplicates by (project_id, domain) — case-insensitive.
        existing = con.execute(
            "SELECT suggestion_id FROM domain_suggestions "
            "WHERE project_id = ? AND lower(domain) = lower(?)",
            (project_id, domain),
        ).fetchone()
        if existing:
            continue
        sid = f"dsg_{uuid.uuid4().hex[:12]}"
        con.execute(
            """INSERT INTO domain_suggestions
               (suggestion_id, project_id, domain, sub_domain,
                rationale, status, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, 'proposed', ?, ?)""",
            (sid, project_id, domain, sub_domain, rationale, now, now),
        )
        n += 1
    return n


def _persist(
    db_path: str, project_id: str, parsed: dict[str, Any],
    obs_ids: list[str],
) -> dict[str, int]:
    with _open(db_path) as con:
        pref_n = _persist_preferences(con, project_id, parsed)
        dom_n = _persist_domain_gap(con, project_id, parsed)
        _mark_consumed(con, obs_ids)
        con.commit()
    return {"preferences": pref_n, "domain_suggestions": dom_n,
            "consumed": len(obs_ids)}


# ─────────── fire-and-forget driver ───────────


async def _run(db_path: str, project_id: str) -> dict[str, int]:
    with _open(db_path) as con:
        observations = _fetch_unconsumed(con, project_id)
    if not observations:
        return {"preferences": 0, "domain_suggestions": 0, "consumed": 0}
    parsed = await extract_batch(
        db_path=db_path, project_id=project_id, observations=observations,
    )
    obs_ids = [o["obs_id"] for o in observations]
    summary = _persist(db_path, project_id, parsed, obs_ids)
    logger.info(
        "edit_learning batch for project %s: prefs=%d, domains=%d, consumed=%d",
        project_id, summary["preferences"],
        summary["domain_suggestions"], summary["consumed"],
    )
    return summary


def fire_batch_extraction(
    *, db_path: str, project_id: str,
    _await: bool = False,
) -> Any:
    """Schedule extraction. Tries the running event loop first; falls
    back to a daemon thread (mirrors ``commit_pipeline`` behaviour).

    ``_await=True`` runs synchronously and returns the summary —
    used by tests so they can assert without juggling loops."""
    import asyncio

    if _await:
        # Synchronous wrapper for tests / callers that want the summary.
        # We deliberately distinguish "no loop here" (use asyncio.run)
        # from "already in a loop" (return the coroutine) so we don't
        # swallow extraction errors as if they were loop conflicts.
        try:
            asyncio.get_running_loop()
            return _run(db_path, project_id)  # caller awaits
        except RuntimeError:
            return asyncio.run(_run(db_path, project_id))

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        def _runner() -> None:
            try:
                asyncio.run(_run(db_path, project_id))
            except Exception as e:
                logger.error("edit_learning batch (thread) failed: %s",
                              e, exc_info=True)
        threading.Thread(
            target=_runner, name="edit_learning_batch", daemon=True,
        ).start()
        return None

    async def _wrap() -> None:
        try:
            await _run(db_path, project_id)
        except Exception as e:
            logger.error("edit_learning batch (task) failed: %s",
                          e, exc_info=True)
    loop.create_task(_wrap())
    return None
