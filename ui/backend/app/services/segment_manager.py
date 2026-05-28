"""SegmentManager — paragraph-level provenance for chapter content.

LOADER_SPEC Loader 9 prerequisite. Lets the current_chapter_draft loader
decide what to preserve / what to invite re-generation for, based on
who wrote each paragraph (user vs AI vs AI-then-edited).

Public surface:

- ``get_segments(db, chapter_id)`` → ordered list of dicts
- ``replace_segments(db, chapter_id, project_id, segments)`` → direct write
- ``update_chapter_content(db, chapter_id, project_id, new_text)`` →
  diff-based re-source: unchanged paragraphs keep their source,
  ai_generated paragraphs that the user edited become ai_user_edited,
  new paragraphs become user_written.
- ``record_ai_generation(db, chapter_id, project_id, segments, generation_id)``
  → label freshly-generated paragraphs as ai_generated.

The save_editor_doc path calls ``update_chapter_content`` whenever it
writes ``chapters.final_text`` so segments stay in sync with the
canonical text.
"""
from __future__ import annotations

import logging
import time
import uuid
from difflib import SequenceMatcher
from typing import Any

from .project_store import open_db, _row_to_dict

logger = logging.getLogger("inkoctobot.services.segment_manager")


_VALID_SOURCES = {"user_written", "ai_generated", "ai_user_edited"}


def _sid() -> str:
    return f"seg_{int(time.time() * 1000)}_{uuid.uuid4().hex[:6]}"


def _split_paragraphs(text: str) -> list[str]:
    """Split text into non-empty paragraphs preserving the order."""
    if not text:
        return []
    return [p.strip() for p in text.split("\n\n") if p and p.strip()]


def get_segments(db_path: str, chapter_id: str) -> list[dict[str, Any]]:
    with open_db(db_path) as con:
        rows = con.execute(
            "SELECT * FROM chapter_segments WHERE chapter_id = ? "
            "ORDER BY sequence_order",
            (chapter_id,),
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


def replace_segments(
    db_path: str, chapter_id: str, project_id: str,
    segments: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Atomic: drop existing rows, write the new list.

    Each ``segments[i]`` must carry ``content`` and ``source``; the
    other columns are optional. ``sequence_order`` is reassigned by
    list index so callers don't have to bookkeep it.
    """
    for s in segments:
        src = s.get("source")
        if src not in _VALID_SOURCES:
            raise ValueError(
                f"source must be one of {sorted(_VALID_SOURCES)}, got {src!r}"
            )
    with open_db(db_path) as con:
        con.execute(
            "DELETE FROM chapter_segments WHERE chapter_id = ?",
            (chapter_id,),
        )
        for i, s in enumerate(segments):
            con.execute(
                """INSERT INTO chapter_segments (
                       segment_id, chapter_id, project_id, sequence_order,
                       content, source, generation_id, original_ai_content,
                       created_at, modified_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?,
                           CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)""",
                (s.get("segment_id") or _sid(), chapter_id, project_id, i,
                 s["content"], s["source"],
                 s.get("generation_id"),
                 s.get("original_ai_content")),
            )
        con.commit()
    return get_segments(db_path, chapter_id)


def update_chapter_content(
    db_path: str, chapter_id: str, project_id: str, new_text: str,
) -> list[dict[str, Any]]:
    """Diff the chapter's existing segments against ``new_text`` and
    persist a new segment list with sources inferred:

    - paragraph identical to an old segment → keep that segment's source
    - paragraph close-but-different to an old ``ai_generated`` segment →
      ``ai_user_edited`` (user touched AI content)
    - paragraph close-but-different to an old ``ai_user_edited`` segment →
      stays ``ai_user_edited``
    - paragraph close-but-different to an old ``user_written`` segment →
      stays ``user_written``
    - paragraph with no close match → ``user_written``
    """
    old_segments = get_segments(db_path, chapter_id)
    new_paragraphs = _split_paragraphs(new_text)

    new_segments: list[dict[str, Any]] = []
    used_old_ids: set[str] = set()

    for p in new_paragraphs:
        match = _best_match(p, old_segments, used_old_ids)
        if match is None:
            new_segments.append({
                "content": p,
                "source": "user_written",
            })
            continue
        old, ratio = match
        used_old_ids.add(old["segment_id"])
        if ratio >= 0.999:
            # Unchanged paragraph — preserve everything.
            new_segments.append({
                "segment_id":          old["segment_id"],
                "content":             p,
                "source":              old["source"],
                "generation_id":       old.get("generation_id"),
                "original_ai_content": old.get("original_ai_content"),
            })
        else:
            # Edited paragraph — keep ai/user provenance but mark as
            # user-touched if it was AI-pure.
            src = old["source"]
            if src == "ai_generated":
                src = "ai_user_edited"
            new_segments.append({
                "content":             p,
                "source":              src,
                "generation_id":       old.get("generation_id"),
                "original_ai_content": old.get("original_ai_content")
                                        or (old["content"] if old["source"] != "user_written" else None),
            })
    return replace_segments(db_path, chapter_id, project_id, new_segments)


def _best_match(
    new_paragraph: str,
    old_segments: list[dict[str, Any]],
    used_old_ids: set[str],
    threshold: float = 0.6,
) -> tuple[dict[str, Any], float] | None:
    """Find the unused old segment most similar to ``new_paragraph``
    via difflib ratio. Returns None if no match clears ``threshold``.
    """
    best: tuple[dict[str, Any], float] | None = None
    for o in old_segments:
        if o["segment_id"] in used_old_ids:
            continue
        ratio = SequenceMatcher(None, o["content"], new_paragraph).ratio()
        if best is None or ratio > best[1]:
            best = (o, ratio)
    if best is None or best[1] < threshold:
        return None
    return best


def record_ai_generation(
    db_path: str, chapter_id: str, project_id: str,
    paragraphs: list[str], generation_id: str | None = None,
) -> list[dict[str, Any]]:
    """Append AI-generated paragraphs as ai_generated segments."""
    existing = get_segments(db_path, chapter_id)
    combined = existing + [
        {
            "content": p,
            "source": "ai_generated",
            "generation_id": generation_id,
            "original_ai_content": p,
        }
        for p in paragraphs
        if p and p.strip()
    ]
    return replace_segments(db_path, chapter_id, project_id, combined)


def delete_segments(db_path: str, chapter_id: str) -> None:
    with open_db(db_path) as con:
        con.execute(
            "DELETE FROM chapter_segments WHERE chapter_id = ?",
            (chapter_id,),
        )
        con.commit()
