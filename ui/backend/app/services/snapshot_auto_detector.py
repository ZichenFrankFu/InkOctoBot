"""SnapshotAutoDetector — async LLM check + safe auto-bind.

LOADER_SPEC Loader 5, Batch 5. After a chapter is committed, every
pending snapshot whose ``expected_chapter_range_start <= chapter_num``
gets a single LLM pass to ask whether its ``trigger_description``
actually fired in the chapter content.

Results with ``triggered=True`` AND ``confidence > 0.7`` become
candidate auto-binds. The detector does NOT auto-write to
``character_snapshots``; the caller (typically the chapter-commit
endpoint) decides whether to apply each candidate, or surface them to
the user for confirmation.

The detector is async + tolerant. LLM / parse failures degrade to
"no candidates" with a debug log — never raises.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from . import snapshot_store

logger = logging.getLogger("inkoctobot.services.snapshot_auto_detector")


_MIN_CONFIDENCE = 0.7
_PROMPT_CONTENT_LIMIT = 4000


def _next_pending_for_character(
    db_path: str, character_id: str,
) -> dict | None:
    """First snapshot whose transition isn't complete yet."""
    for s in snapshot_store.list_snapshots(db_path, character_id):
        if s.get("transition_complete_chapter") is None:
            return s
    return None


def _build_prompt(
    snapshot: dict, character_name: str, chapter_content: str,
) -> str:
    return (
        "请判断以下章节内容是否触发了角色【" + character_name + "】的预设转变。\n\n"
        "预设触发描述：\n" + (snapshot.get("trigger_description") or "（无）") + "\n\n"
        "章节内容（截取）：\n"
        + (chapter_content or "")[:_PROMPT_CONTENT_LIMIT] + "\n\n"
        "判断标准：\n"
        "- 触发 = 该章节出现了与「触发描述」直接对应的事件或证据\n"
        "- 未触发 = 暗示、伏笔、相关线索都不算\n\n"
        "严格 JSON 输出（不带额外说明）：\n"
        '{"triggered": true/false, "confidence": 0.0-1.0, "reason": "..."}\n'
    )


def _parse_response(text: str) -> dict | None:
    """Extract ``{"triggered": bool, "confidence": float, "reason": ...}``
    from an LLM response. Tolerates fenced code blocks and prose."""
    if not text:
        return None
    snippet = text.strip()
    if "```" in snippet:
        parts = snippet.split("```")
        for part in parts:
            cleaned = part.strip()
            if cleaned.startswith("json"):
                cleaned = cleaned[len("json"):].strip()
            if cleaned.startswith("{"):
                snippet = cleaned
                break
    if "{" in snippet and "}" in snippet:
        snippet = snippet[snippet.index("{"): snippet.rindex("}") + 1]
    try:
        parsed = json.loads(snippet)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    try:
        return {
            "triggered":  bool(parsed.get("triggered")),
            "confidence": max(0.0, min(1.0, float(parsed.get("confidence") or 0.0))),
            "reason":     str(parsed.get("reason") or "").strip(),
        }
    except (TypeError, ValueError):
        return None


async def detect_after_chapter_commit(
    router: Any,
    db_path: str,
    project_id: str,
    chapter_num: int,
    chapter_content: str,
    *,
    on_stage_characters: list[str],
) -> list[dict[str, Any]]:
    """Scan every on-stage character for a pending snapshot whose
    trigger looks satisfied in ``chapter_content``. Returns the list
    of candidate matches.

    Each candidate dict carries enough fields for the caller to apply
    or surface as a user-confirm prompt:
        {
          "character_id": ..., "character_name": ...,
          "snapshot_id": ..., "snapshot_order": ...,
          "trigger": ..., "confidence": ..., "reason": ...,
          "chapter_num": ...,
        }
    """
    results: list[dict[str, Any]] = []
    try:
        from . import project_store as _ps
        chars = _ps.list_characters(db_path, project_id)
    except Exception as e:
        logger.debug("character list failed: %s", e)
        return results
    name_to_char = {c.get("name", ""): c for c in chars}

    for char_name in on_stage_characters or []:
        char = name_to_char.get(char_name)
        if not char:
            continue
        pending = _next_pending_for_character(db_path, char["id"])
        if pending is None:
            continue
        start = pending.get("expected_chapter_range_start")
        if start is not None and chapter_num < int(start):
            continue
        # Skip if the snapshot is already bound to this chapter.
        if int(chapter_num) in [
            int(c) for c in (pending.get("bound_chapters") or [])
        ]:
            continue
        try:
            prompt = _build_prompt(pending, char_name, chapter_content)
            resp = await router.generate(messages=[
                {"role": "user", "content": prompt},
            ])
            text = getattr(resp, "content", "") or ""
            parsed = _parse_response(text)
        except Exception as e:
            logger.debug("auto_detector LLM failed for %s: %s",
                          pending.get("snapshot_id"), e)
            continue
        if parsed is None:
            continue
        if not parsed["triggered"]:
            continue
        if parsed["confidence"] < _MIN_CONFIDENCE:
            continue
        results.append({
            "character_id":   char["id"],
            "character_name": char_name,
            "snapshot_id":    pending["snapshot_id"],
            "snapshot_order": pending["snapshot_order"],
            "trigger":        pending.get("trigger_description") or "",
            "confidence":     parsed["confidence"],
            "reason":         parsed["reason"],
            "chapter_num":    chapter_num,
        })
    return results


def auto_bind(
    db_path: str, snapshot_id: str, chapter_num: int,
) -> dict | None:
    """Bind ``chapter_num`` to ``snapshot_id`` only if it wouldn't
    overwrite a user-managed snapshot.

    Snapshots already marked ``bound_by='user'`` are left alone so the
    auto-detector can't reshape user-driven planning. New rows get
    ``bound_by='auto'``.
    """
    snap = snapshot_store.get_snapshot(db_path, snapshot_id)
    if snap is None:
        return None
    # A snapshot is "user-managed" once the user has actively bound at
    # least one chapter — only then do we refuse auto edits. Fresh
    # snapshots (default bound_by='user' but no bindings yet) are open
    # to the detector.
    if snap.get("bound_by") == "user" and (snap.get("bound_chapters") or []):
        logger.debug("auto_bind skipped: snapshot %s is user-managed", snapshot_id)
        return snap
    return snapshot_store.bind_chapter(
        db_path, snapshot_id, chapter_num, source="auto",
    )
