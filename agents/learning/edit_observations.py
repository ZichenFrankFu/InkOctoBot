"""Cheap per-commit capture of user edits.

We deliberately do NOT call an LLM here. Each commit-time entry just
records the AI baseline + user-edited text + special_requirement + a
few computed diff stats. Once enough rows accumulate (the threshold
lives in ``settings.json``), :func:`maybe_capture_and_fire` schedules
a single batched LLM extraction over the whole window.

Why batched, not per-edit:
- One edit is noise; cross-chapter trends are signal.
- One LLM call per N chapters is dramatically cheaper.
- The batch extraction is also where Part B (domain knowledge gap
  detection) gets its prompt input for free.

Source gate: only edits whose source is exactly ``user_edit`` are
captured. AI outputs (``ai`` / ``ai_generated``) are never folded into
"user preferences" — otherwise we'd be learning from our own output.
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
import time
import uuid
from typing import Any

logger = logging.getLogger("inkoctobot.agents.learning.edit_observations")


_USER_EDIT_SOURCES = {"user_edit", "user_edited"}


def _now() -> float:
    return time.time()


def _new_obs_id() -> str:
    return f"obs_{uuid.uuid4().hex[:12]}"


def _is_user_edit(source: str) -> bool:
    return (source or "").strip().lower() in _USER_EDIT_SOURCES


# ─────────── diff stats (no LLM) ───────────


_PARA_SPLIT = re.compile(r"\n\s*\n")
# A dialogue line, heuristically: starts with 「 or " or contains 」/"
_DIALOGUE_LINE = re.compile(r'[「"][^」"\n]{1,200}[」"]')


def _dialogue_ratio(text: str) -> float:
    """Fraction of paragraphs that look like dialogue. Cheap heuristic."""
    paras = [p.strip() for p in _PARA_SPLIT.split(text or "") if p.strip()]
    if not paras:
        return 0.0
    dialogue = sum(1 for p in paras if _DIALOGUE_LINE.search(p))
    return round(dialogue / len(paras), 3)


def compute_diff_stats(original: str, edited: str) -> dict[str, Any]:
    """Compute lightweight, deterministic diff metrics.

    The point isn't precision — we just need a few numbers the batch
    extractor's prompt can summarise as "the user trims dialogue",
    "the user adds setting", etc. without us paying for tokenization.
    """
    orig_len = len(original or "")
    edit_len = len(edited or "")
    delta = edit_len - orig_len
    orig_paras = len([p for p in _PARA_SPLIT.split(original or "") if p.strip()])
    edit_paras = len([p for p in _PARA_SPLIT.split(edited or "") if p.strip()])
    return {
        "original_chars":       orig_len,
        "edited_chars":         edit_len,
        "char_delta":           delta,
        "char_delta_pct":       round(delta / orig_len, 3) if orig_len else 0.0,
        "original_paragraphs":  orig_paras,
        "edited_paragraphs":    edit_paras,
        "original_dialogue_ratio": _dialogue_ratio(original or ""),
        "edited_dialogue_ratio":   _dialogue_ratio(edited or ""),
    }


# ─────────── DB access ───────────


def _open(db_path: str) -> sqlite3.Connection:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    return con


def _latest_ai_version(
    con: sqlite3.Connection, chapter_id: str,
) -> str | None:
    """Return the content of the most recent ``source='ai'`` version
    for the chapter — the baseline against which the user edit is the
    delta. ``None`` when no AI version exists yet (first save, manual
    chapter, etc.)."""
    if not chapter_id:
        return None
    row = con.execute(
        """SELECT content FROM text_versions
           WHERE chapter_id = ? AND source = 'ai'
           ORDER BY version_num DESC LIMIT 1""",
        (chapter_id,),
    ).fetchone()
    return row["content"] if row else None


def _special_requirement_for(
    con: sqlite3.Connection, chapter_id: str,
) -> str:
    """Read ``chapters.special_requirements`` for the chapter. Empty
    when missing or the column is NULL."""
    if not chapter_id:
        return ""
    try:
        row = con.execute(
            "SELECT special_requirements FROM chapters WHERE chapter_id = ?",
            (chapter_id,),
        ).fetchone()
    except sqlite3.OperationalError:
        return ""
    if not row:
        return ""
    return (row["special_requirements"] or "").strip()


# ─────────── public capture ───────────


def capture_observation(
    *,
    db_path: str,
    project_id: str,
    chapter_id: str,
    chapter_num: int,
    edited_text: str,
    original_text: str | None = None,
    special_requirement: str | None = None,
) -> str | None:
    """Insert one ``edit_observations`` row.

    Returns the new ``obs_id`` on success, or ``None`` when the capture
    was intentionally skipped (e.g. no AI baseline to diff against —
    we don't fabricate one). All errors are caught and logged; this
    helper never raises into the commit path."""
    if not db_path or not project_id or not chapter_id:
        return None
    try:
        with _open(db_path) as con:
            base = (
                original_text
                if original_text is not None
                else _latest_ai_version(con, chapter_id)
            )
            if base is None:
                # No AI baseline → nothing meaningful to diff against.
                logger.debug(
                    "capture skipped — no ai baseline for chapter %s",
                    chapter_id,
                )
                return None
            sr = (
                special_requirement
                if special_requirement is not None
                else _special_requirement_for(con, chapter_id)
            )
            stats = compute_diff_stats(base, edited_text or "")
            obs_id = _new_obs_id()
            con.execute(
                """INSERT INTO edit_observations
                   (obs_id, project_id, chapter_id, chapter_num,
                    original_text, edited_text, special_requirement,
                    diff_stats_json, consumed, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?)""",
                (
                    obs_id, project_id, chapter_id, int(chapter_num or 0),
                    base, edited_text or "", sr,
                    json.dumps(stats, ensure_ascii=False), _now(),
                ),
            )
            con.commit()
            return obs_id
    except Exception as e:
        logger.error("capture_observation failed: %s", e, exc_info=True)
        return None


def count_unconsumed(db_path: str, project_id: str) -> int:
    """Number of ``consumed=0`` observations for the project."""
    if not db_path or not project_id:
        return 0
    try:
        with _open(db_path) as con:
            row = con.execute(
                "SELECT COUNT(*) AS n FROM edit_observations "
                "WHERE project_id = ? AND consumed = 0",
                (project_id,),
            ).fetchone()
        return int(row["n"] or 0)
    except Exception as e:
        logger.debug("count_unconsumed failed: %s", e)
        return 0


def maybe_capture_and_fire(
    *,
    db_path: str,
    project_id: str,
    chapter_id: str,
    chapter_num: int,
    source: str,
    edited_text: str,
    original_text: str | None = None,
    special_requirement: str | None = None,
) -> None:
    """Public entry point from ``save_version`` and friends.

    Runs the source gate, captures the observation, and if the
    unconsumed count reaches the configured threshold schedules a
    fire-and-forget batch extraction. Never raises — the commit path
    is sacred."""
    if not _is_user_edit(source):
        return
    obs_id = capture_observation(
        db_path=db_path,
        project_id=project_id,
        chapter_id=chapter_id,
        chapter_num=chapter_num,
        edited_text=edited_text,
        original_text=original_text,
        special_requirement=special_requirement,
    )
    if not obs_id:
        return

    # Threshold check.
    try:
        from ui.backend.app.settings import get_edit_learning_threshold
        threshold = get_edit_learning_threshold()
    except Exception:
        threshold = 5

    n = count_unconsumed(db_path, project_id)
    if n < threshold:
        return

    # Fire batch extraction (best-effort, never blocks the caller).
    try:
        from .edit_batch_extractor import fire_batch_extraction
        fire_batch_extraction(db_path=db_path, project_id=project_id)
    except Exception as e:
        logger.error("fire_batch_extraction failed: %s", e, exc_info=True)
