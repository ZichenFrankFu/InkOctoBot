"""snapshot_detector — detect satisfied character snapshot transitions.

Wraps the existing :func:`services.snapshot_auto_detector.detect_after_chapter_commit`
which was already implemented but never wired up. For each candidate
match above a confidence threshold, emits a medium-priority
user_notification so the user can confirm + bind to the chapter.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from .summarizer import _resolve_chapter_num, _resolve_chapter_text

logger = logging.getLogger("inkoctobot.commit_pipeline.snapshot_detector")

if TYPE_CHECKING:
    from . import SubTaskContext


_CONFIDENCE_THRESHOLD = 0.7


def _resolve_on_stage_characters(ctx: "SubTaskContext") -> list[str]:
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
    on_stage = _resolve_on_stage_characters(ctx)
    if not on_stage:
        return {"status": "skipped", "reason": "no_on_stage_characters"}

    # The existing detector accepts a "router" argument for optional
    # LLM-assisted matching but tolerates None — we pass the
    # FallbackRouter so any LLM scoring it does uses the post_commit
    # binding.
    router = None
    try:
        from llm.fallback_router import get_fallback_router
        router = get_fallback_router()
    except Exception:
        pass

    try:
        from ui.backend.app.services.snapshot_auto_detector import (
            detect_after_chapter_commit,
        )
        candidates = await detect_after_chapter_commit(
            router=router,
            db_path=ctx.db_path,
            project_id=ctx.project_id,
            chapter_num=chapter_num,
            chapter_content=chapter_text,
            on_stage_characters=on_stage,
        )
    except Exception as e:
        logger.warning("snapshot_auto_detector failed: %s", e)
        raise

    # Emit notification for any high-confidence candidate.
    notified = 0
    if candidates:
        try:
            from ui.backend.app.services.notifications import create_notification
            for c in candidates:
                conf = float(c.get("confidence") or 0.0)
                if conf < _CONFIDENCE_THRESHOLD:
                    continue
                create_notification(
                    ctx.db_path,
                    project_id=ctx.project_id,
                    chapter_id=ctx.chapter_id,
                    notification_type="snapshot_trigger_detected",
                    severity="medium",
                    title=f"角色快照触发：{c.get('character_name', '')}",
                    description=(
                        f"本章可能触发角色 {c.get('character_name', '')} 的"
                        f" snapshot（confidence {conf:.2f}）。"
                        f"原因：{c.get('reason', '')}"
                    ),
                    action_label="查看 pending snapshots",
                    action_url="/characters/snapshots",
                    related_entity_id=c.get("snapshot_id", ""),
                )
                notified += 1
        except Exception as e:
            logger.debug("snapshot notification skipped: %s", e)

    return {
        "status":      "ok",
        "candidates":  len(candidates),
        "notified":    notified,
    }
