"""skill_emitter — learn writing skills from user edit deltas.

Opt-in (default_enabled=False per the registry). Compares the latest
committed version of the chapter against the second-latest; if the
diff is substantive enough, asks the LLM to extract one named
"writing skill" pattern and stages it as a SKILL_*.md candidate
(writes to disk under ``agents/learned_skills/``).

Phase 5 minimal implementation: produces a placeholder SKILL stub
when no diff is available; full diff-based learning is left to a
follow-up since it interacts with the existing reference_ingest
SkillEmitter (which is corpus-scoped and would need a per-chapter
adapter).
"""
from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .summarizer import _resolve_chapter_num

logger = logging.getLogger("inkoctobot.commit_pipeline.skill_emitter")

if TYPE_CHECKING:
    from . import SubTaskContext


def _get_recent_versions(db_path: str, chapter_id: str, n: int = 2) -> list[str]:
    """Pull the most recent ``n`` versions' content for diff comparison."""
    try:
        with sqlite3.connect(db_path) as con:
            rows = con.execute(
                "SELECT content FROM text_versions WHERE chapter_id = ? "
                "ORDER BY version_num DESC LIMIT ?",
                (chapter_id, n),
            ).fetchall()
        return [r[0] for r in rows if r and r[0]]
    except Exception as e:
        logger.debug("version fetch failed: %s", e)
        return []


async def run(ctx: "SubTaskContext") -> dict[str, Any]:
    versions = _get_recent_versions(ctx.db_path, ctx.chapter_id, n=2)
    if len(versions) < 2:
        return {
            "status":  "skipped",
            "reason":  "need_at_least_two_versions_to_diff",
            "versions_found": len(versions),
        }
    latest, prior = versions
    # Diff heuristic: if < 50 chars changed, not a learnable edit.
    diff_size = abs(len(latest) - len(prior))
    if diff_size < 50:
        return {
            "status":    "skipped",
            "reason":    "diff_too_small",
            "diff_size": diff_size,
        }

    # Stage a SKILL_*.md placeholder describing the diff. Real
    # LLM-based extraction is the follow-up work — this gives the user
    # something visible to act on.
    #
    # Path resolution: honor WN_DATA_DIR (test-mode override) so unit
    # tests don't leak into agents/learned_skills/ in the real repo.
    try:
        import os
        env = os.environ.get("WN_DATA_DIR")
        if env:
            skills_dir = Path(env) / "learned_skills"
        else:
            repo_root = Path(__file__).resolve().parents[6]
            skills_dir = repo_root / "agents" / "learned_skills"
        skills_dir.mkdir(parents=True, exist_ok=True)
        chapter_num = _resolve_chapter_num(ctx)
        skill_path = skills_dir / f"SKILL_pending_ch{chapter_num}.md"
        skill_path.write_text(
            f"# Pending skill — chapter {chapter_num}\n\n"
            f"User-edit diff size: {diff_size} chars.\n"
            f"This file is a placeholder. Implementation follow-up: "
            f"call the LLM to extract a named writing-skill pattern from "
            f"the diff between the latest two versions of "
            f"{ctx.chapter_id} and write the result here.\n",
            encoding="utf-8",
        )
    except Exception as e:
        logger.warning("skill stub write failed: %s", e)
        return {"status": "ok", "diff_size": diff_size, "skill_written": False}

    return {
        "status":         "ok",
        "diff_size":      diff_size,
        "skill_written":  True,
        "skill_path":     str(skill_path),
    }
