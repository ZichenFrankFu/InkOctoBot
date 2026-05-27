"""Current chapter draft loader (LOADER_SPEC Loader 9).

Decides what existing content in the chapter under edit to surface to
the Writer, based on the user's chosen generation mode. The key
principle: ``user_written`` content is always valuable; pure
``ai_generated`` paragraphs in a ``fresh`` regeneration are filtered
out so the model doesn't anchor to a draft the user just discarded.

Four modes (matching the public API):

- ``fresh``           — start over; show only user-touched paragraphs
                        and inject anti-hints from recent failures.
- ``continue``        — write past the end; show the tail.
- ``rewrite_from``    — keep the prefix; rewrite from anchor onward.
- ``modify_section``  — keep before+after; replace a middle range.

Depends on ``segment_manager`` (Batch 2) for the paragraph-source data
and ``failure_analyzer`` (Batch 2) for the anti-hint corpus.
"""
from __future__ import annotations

import logging
from typing import Any

from ..budgets import BUDGETS
from ..utils import clip, section

logger = logging.getLogger("inkoctobot.services.prompt_context.current_chapter_draft")


_VALID_MODES = {"fresh", "continue", "rewrite_from", "modify_section"}

# Tail size for continue mode. Roughly half the block budget — leaves
# room for the directive while keeping enough context to flow.
_CONTINUE_TAIL_CHARS = 3500


def load(
    project_id: str,
    chapter_id: str = "",
    *,
    generation_mode: str = "fresh",
    revision_anchor: dict[str, Any] | None = None,
    include_failed_drafts_as_hint: bool = True,
    exclude: set | None = None,
) -> str:
    """Render the existing-content block for the chosen mode."""
    if not chapter_id:
        return ""
    mode = generation_mode if generation_mode in _VALID_MODES else "fresh"
    try:
        from ui.backend.app.services import segment_manager
        from ui.backend.app.services.project_paths import get_db_path

        db_path = get_db_path()
        segments = segment_manager.get_segments(db_path, chapter_id)
        if mode == "fresh":
            return _render_fresh(db_path, chapter_id, segments,
                                  include_failed_drafts_as_hint)
        if not segments:
            return ""
        if mode == "continue":
            return _render_continue(segments)
        if mode == "rewrite_from":
            return _render_rewrite_from(segments, revision_anchor or {})
        if mode == "modify_section":
            return _render_modify_section(segments, revision_anchor or {})
        return ""
    except Exception as e:
        logger.debug("current_chapter_draft skipped: %s", e)
        return ""


# ─────────── fresh ───────────

def _detect_gaps(positions: list[int]) -> list[tuple[int, int]]:
    """Find gaps in the kept-segment positions. Returns a list of
    ``(start, end)`` inclusive ranges of missing indices.
    """
    if not positions:
        return []
    sorted_pos = sorted(set(positions))
    gaps: list[tuple[int, int]] = []
    last = sorted_pos[0]
    if last > 0:
        gaps.append((0, last - 1))
    for p in sorted_pos[1:]:
        if p > last + 1:
            gaps.append((last + 1, p - 1))
        last = p
    return gaps


def _render_fresh(
    db_path: str, chapter_id: str, segments: list[dict],
    include_failed_hint: bool,
) -> str:
    """Show only user-touched paragraphs + anti-hints from rejections."""
    valuable = [
        s for s in segments
        if s.get("source") in ("user_written", "ai_user_edited")
    ]
    parts: list[str] = []
    if valuable:
        parts.append("[本章已有用户保留的内容，新生成必须保持与这些内容的连贯]")
        for seg in valuable:
            label = (
                "user 手写" if seg["source"] == "user_written"
                else "user 编辑过"
            )
            parts.append(f"\n[段落 {seg['sequence_order']}（{label}）]")
            parts.append(seg.get("content") or "")
        gap_ranges = _detect_gaps([s["sequence_order"] for s in valuable])
        if gap_ranges:
            parts.append(
                "\n[空缺位置：" +
                ", ".join(
                    f"段落 {a}" if a == b else f"段落 {a}-{b}"
                    for a, b in gap_ranges
                ) +
                " 需要新生成]"
            )

    if include_failed_hint:
        try:
            from ui.backend.app.services import failure_analyzer
            failed = failure_analyzer.get_recent_failed_generations(
                db_path, chapter_id, limit=2,
            )
        except Exception as e:
            logger.debug("fetch failed generations skipped: %s", e)
            failed = []
        anti_hints: list[str] = []
        for f in failed:
            issues = f.get("issues_detected") or []
            for it in issues[:3]:
                s = str(it or "").strip()
                if s:
                    anti_hints.append(s)
        if anti_hints:
            if parts:
                parts.append("\n[请避免以下方向]")
            else:
                parts.append("[上次生成方向 user 不满意，请避免以下问题]")
            for h in anti_hints[:6]:
                parts.append(f"- 避免：{h}")

    if not parts:
        return ""
    body = clip("\n".join(parts), BUDGETS["current_chapter_draft"])
    return section("本章已有内容", body)


# ─────────── continue ───────────

def _render_continue(segments: list[dict]) -> str:
    """Take the tail (≤ ``_CONTINUE_TAIL_CHARS``) so the Writer can pick
    up where the existing text leaves off.
    """
    total = sum(len(s.get("content") or "") for s in segments)
    selected: list[dict] = []
    running = 0
    for seg in reversed(segments):
        body = seg.get("content") or ""
        if selected and running + len(body) > _CONTINUE_TAIL_CHARS:
            break
        selected.insert(0, seg)
        running += len(body)
    if not selected:
        selected = [segments[-1]]

    truncated = len(selected) < len(segments)
    parts = [f"[本章已写 {total} 字符，请从最后段之后续写]"]
    if truncated:
        parts.append(f"\n...（前 {len(segments) - len(selected)} 段省略）")
    for seg in selected:
        body = seg.get("content") or ""
        if seg.get("source") == "user_written":
            parts.append(f"\n[user 手写]\n{body}")
        else:
            parts.append(f"\n{body}")
    text = clip("\n".join(parts), BUDGETS["current_chapter_draft"])
    return section("本章已有正文（续写）", text)


# ─────────── rewrite_from ───────────

def _resolve_rewrite_anchor(segments: list[dict], anchor: dict) -> int:
    """Return the segment index from which to rewrite.

    Supports:
      - ``paragraph_index`` (int) — direct
      - ``anchor_text`` (str) — substring match against segment content

    Out-of-range / missing anchor → ``len(segments)`` which makes
    rewrite_from degrade to "continue from the end".
    """
    if not anchor:
        return len(segments)
    if "paragraph_index" in anchor:
        try:
            idx = int(anchor["paragraph_index"])
        except (TypeError, ValueError):
            return len(segments)
        return max(0, min(idx, len(segments)))
    needle = (anchor.get("anchor_text") or "").strip()
    if needle:
        for i, seg in enumerate(segments):
            if needle in (seg.get("content") or ""):
                return i
    return len(segments)


def _render_rewrite_from(segments: list[dict], anchor: dict) -> str:
    rewrite_from = _resolve_rewrite_anchor(segments, anchor)
    kept = segments[:rewrite_from]
    discarded = segments[rewrite_from:]

    parts: list[str] = []
    if kept:
        parts.append(f"[user 决定从第 {rewrite_from + 1} 段开始重写]")
        parts.append(f"\n[保留部分（{len(kept)} 段，必须保持连贯）]")
        for seg in kept:
            parts.append(seg.get("content") or "")
        tail = (kept[-1].get("content") or "")[-50:]
        parts.append(f"\n[请从「{tail}」之后续写新内容]")
    else:
        parts.append("[user 决定从本章开头重写]")
        parts.append("\n[请从本章开头开始写新内容]")

    if discarded:
        discard_head = (discarded[0].get("content") or "")[:100]
        parts.append(
            f"\n[避免重复方向：原本第 {rewrite_from + 1} 段开头是"
            f"「{discard_head}...」，请换个写法]"
        )

    body = clip("\n".join(parts), BUDGETS["current_chapter_draft"])
    return section("本章已有正文（保留前段，重写后段）", body)


# ─────────── modify_section ───────────

def _render_modify_section(segments: list[dict], anchor: dict) -> str:
    try:
        start = int(anchor.get("start_paragraph", 0))
    except (TypeError, ValueError):
        start = 0
    try:
        end = int(anchor.get("end_paragraph", start))
    except (TypeError, ValueError):
        end = start
    start = max(0, min(start, max(0, len(segments) - 1)))
    end = max(start, min(end, max(0, len(segments) - 1)))
    instruction = (anchor.get("modification_instruction") or "").strip()

    before = segments[:start]
    target = segments[start:end + 1]
    after = segments[end + 1:]

    parts: list[str] = []
    if before:
        parts.append("[前文 - 保留]")
        for seg in before:
            parts.append(seg.get("content") or "")
    parts.append(
        f"\n[待修改段落（第 {start + 1}-{end + 1} 段，原内容）]"
    )
    for seg in target:
        parts.append(seg.get("content") or "")
    if instruction:
        parts.append(f"\n[修改要求]\n{instruction}")
    if after:
        parts.append("\n[后文 - 保留，新修改的内容需要与之自然连接]")
        for seg in after:
            parts.append(seg.get("content") or "")

    body = clip("\n".join(parts), BUDGETS["current_chapter_draft"])
    return section("本章已有正文（局部修改）", body)
