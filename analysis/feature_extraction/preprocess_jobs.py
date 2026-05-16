"""In-memory preprocess-job registry.

A preprocess job parses the work's raw text into chapters, applies the
author-note heuristic, and reports live progress so the UI can show
"现在处理到第 X 章 / 共 N 章" with pause/resume controls.

Pause/resume granularity: **chapter boundary** — the job checks the
pause event after each chapter, so a paused job has already flushed
the work it did and resumes cleanly from the next chapter.

The job state lives in process memory only — restarting the server drops
in-flight jobs (rare; preprocessing is fast). Final results are written
to the work's segments_json so downstream code (segment planner,
extractor) sees them.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger("inkoctobot.analysis.preprocess_jobs")


@dataclass
class LogEntry:
    ts: float
    message: str
    chapter: Optional[int] = None  # 1-based chapter number, if applicable

    def to_dict(self) -> dict:
        return {"ts": self.ts, "message": self.message, "chapter": self.chapter}


@dataclass
class PreprocessJob:
    ref_id: str
    state: str = "idle"  # idle | running | paused | done | error | cancelled
    current_chapter: int = 0
    total_chapters: int = 0
    detected_pattern: Optional[str] = None
    flagged_count: int = 0
    log: list[LogEntry] = field(default_factory=list)
    error: Optional[str] = None
    started_at: float = 0.0
    ended_at: float = 0.0
    # Result of detection (set when job completes)
    chapters: list[dict] = field(default_factory=list)
    candidates: list[dict] = field(default_factory=list)
    fallback_used: bool = False

    # Control primitives (not serialized). threading.Event is loop-agnostic,
    # so the endpoint handlers (which run in FastAPI's threadpool) can flip
    # pause/resume without needing to interact with the asyncio loop directly.
    _resume: Optional[threading.Event] = None
    _cancel: bool = False
    _task: Optional[asyncio.Task] = None

    def append_log(self, message: str, chapter: Optional[int] = None) -> None:
        self.log.append(LogEntry(ts=time.time(), message=message, chapter=chapter))
        # Cap log to avoid unbounded growth on huge works
        if len(self.log) > 1000:
            self.log = self.log[-800:]

    def to_status(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "current_chapter": self.current_chapter,
            "total_chapters": self.total_chapters,
            "detected_pattern": self.detected_pattern,
            "flagged_count": self.flagged_count,
            "log": [e.to_dict() for e in self.log[-60:]],  # last 60 lines for UI
            "error": self.error,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "fallback_used": self.fallback_used,
            "candidates": self.candidates,
        }


_jobs: dict[str, PreprocessJob] = {}


def get_job(ref_id: str) -> Optional[PreprocessJob]:
    return _jobs.get(ref_id)


def get_or_create(ref_id: str) -> PreprocessJob:
    if ref_id not in _jobs:
        _jobs[ref_id] = PreprocessJob(ref_id=ref_id)
    return _jobs[ref_id]


def clear(ref_id: str) -> None:
    _jobs.pop(ref_id, None)


async def _run_detection(job: PreprocessJob, text: str,
                          per_chapter_delay_ms: int = 0,
                          extra_patterns: list[dict] | None = None) -> None:
    """Body of the preprocess job. Runs in its own task. Honors job._cancel
    and job._resume between chapters so the UI's pause/resume controls
    take effect at chapter boundaries (per user's chosen granularity)."""
    from analysis.feature_extraction.chapter_parser import (
        detect_chapters, flag_author_notes,
    )

    try:
        job.state = "running"
        job.started_at = time.time()
        job.append_log("开始解析章节…")

        # Phase 1: chapter detection (single regex pass per pattern; fast)
        result = detect_chapters(text, extra_patterns=extra_patterns)
        chapters = result["chapters"]
        job.detected_pattern = result["pattern"]
        job.fallback_used = result["fallback_used"]
        job.candidates = result["candidates"]
        job.total_chapters = len(chapters)
        if result["fallback_used"]:
            job.append_log(
                f"未识别到清晰的章节结构，已按 ~3000 字切块（共 {len(chapters)} 块）。"
            )
        else:
            job.append_log(
                f"识别格式：{result['pattern']} · 共 {len(chapters)} 章。"
            )

        # Phase 2: author-note heuristic, walked chapter-by-chapter so the
        # UI can show progress and the user can pause/cancel.
        for i, c in enumerate(chapters):
            # Pause / cancel check at chapter boundary
            if job._cancel:
                job.state = "cancelled"
                job.append_log("已取消。")
                return
            if job._resume is not None and not job._resume.is_set():
                job.append_log("已暂停。")
                # Wait until resumed (or cancelled)
                while job._resume is not None and not job._resume.is_set():
                    if job._cancel:
                        job.state = "cancelled"
                        job.append_log("暂停时被取消。")
                        return
                    await asyncio.sleep(0.2)
                job.append_log("已恢复。")

            # Per-chapter work — for the heuristic this is microseconds,
            # so we batch the actual classification at the end and use
            # this loop primarily for progress reporting.
            job.current_chapter = c.get("number") or (i + 1)
            if (i + 1) % max(1, len(chapters) // 50) == 0 or i == 0 or i == len(chapters) - 1:
                job.append_log(f"处理到第 {job.current_chapter} 章", chapter=job.current_chapter)

            if per_chapter_delay_ms > 0:
                await asyncio.sleep(per_chapter_delay_ms / 1000)
            else:
                # Yield control to event loop so pause/cancel signals are
                # processed and other requests don't starve.
                await asyncio.sleep(0)

        # Heuristic application (in-place; very fast)
        flag_author_notes(chapters)
        flagged = [c for c in chapters if c.get("is_author_note")]
        job.flagged_count = len(flagged)
        job.append_log(
            f"完成。识别 {len(chapters)} 章，疑似作者题外话 {len(flagged)} 章。"
        )

        job.chapters = chapters
        job.state = "done"
        job.ended_at = time.time()
    except Exception as e:
        logger.exception("[preprocess] job for %s failed", job.ref_id)
        job.state = "error"
        job.error = str(e)
        job.ended_at = time.time()
        job.append_log(f"出错：{e}")


async def start_job(ref_id: str, text: str,
                     per_chapter_delay_ms: int = 0,
                     extra_patterns: list[dict] | None = None) -> PreprocessJob:
    """Idempotent: returns the existing running/paused job for ref_id, or
    creates a fresh one and schedules it on the current event loop.

    Must be awaited from an async context (FastAPI ``async def`` handler)
    so ``asyncio.create_task`` has a running loop to attach to.
    """
    job = _jobs.get(ref_id)
    if job and job.state in ("running", "paused"):
        return job
    # Reset for a fresh run
    job = PreprocessJob(ref_id=ref_id)
    job._resume = threading.Event()
    job._resume.set()  # not paused
    job.state = "running"  # set before scheduling so the first status poll already shows running
    _jobs[ref_id] = job
    job._task = asyncio.create_task(
        _run_detection(job, text, per_chapter_delay_ms, extra_patterns),
    )
    return job


def pause_job(ref_id: str) -> bool:
    job = _jobs.get(ref_id)
    if not job or job.state != "running":
        return False
    if job._resume is not None:
        job._resume.clear()
    job.state = "paused"
    return True


def resume_job(ref_id: str) -> bool:
    job = _jobs.get(ref_id)
    if not job or job.state != "paused":
        return False
    if job._resume is not None:
        job._resume.set()
    job.state = "running"
    return True


def cancel_job(ref_id: str) -> bool:
    job = _jobs.get(ref_id)
    if not job:
        return False
    job._cancel = True
    if job._resume is not None:
        job._resume.set()  # wake up paused loop so it can see cancel
    return True


def persist_result_to_segments(ref_id: str, db_path: str,
                                chapters: list[dict]) -> None:
    """Stash detection results into segments_json["preprocess"] so the
    PreprocessPanel UI can pick them up on the next status poll without
    re-running the job."""
    from rag.reference_db import ReferenceDB
    rdb = ReferenceDB(db_path)
    work = rdb.get_work(ref_id)
    if not work:
        return
    try:
        state = json.loads(work.get("segments_json") or "{}")
    except Exception:
        state = {}
    if not isinstance(state, dict):
        state = {}
    # Strip heavy content — keep metadata only for the segments_json blob.
    light = [
        {k: c.get(k) for k in (
            "number", "title", "title_only", "raw_marker", "pattern", "volume",
            "is_author_note", "author_note_score", "author_note_reasons",
        )} | {"char_count": len(c.get("content") or "")}
        for c in chapters
    ]
    state["preprocess"] = {
        "chapters": light,
        "total_chapters": len(light),
        "flagged_count": sum(1 for c in light if c.get("is_author_note")),
    }
    rdb.update_work(ref_id, segments_json=json.dumps(state, ensure_ascii=False))
