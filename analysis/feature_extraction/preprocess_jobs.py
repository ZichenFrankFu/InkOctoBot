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
    # Sub-phase within ``running``. Lets the UI distinguish "still
    # loading the file" from "scanning patterns" from "tagging chapters"
    # — without phases the user sees state="running" with 0/0 progress
    # while a big file is being read in a thread and assumes the app
    # is frozen.
    phase: str = ""  # loading | matching | tagging | finalizing | ""
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
            "phase": self.phase,
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


# ── Sample-based file reading (for fast format guessing on big works) ──

def _safe_decode(raw: bytes) -> str:
    """Decode UTF-8 ignoring partial chars at chunk boundaries. Falls
    back to GB18030, then errors='ignore' as last resort."""
    if not raw:
        return ""
    for trim in (0, 1, 2, 3, 4):
        try:
            return raw[:len(raw) - trim].decode("utf-8") if trim else raw.decode("utf-8")
        except UnicodeDecodeError:
            continue
    try:
        return raw.decode("gb18030")
    except UnicodeDecodeError:
        return raw.decode("utf-8", errors="ignore")


def read_sample(file_path: str, total_bytes: int = 2_500_000) -> str:
    """Read 3 disjoint chunks (head / middle / tail) totalling ~``total_bytes``
    and concatenate with paragraph breaks. For format detection — most
    works have consistent chapter markers throughout, so sampling three
    windows gives a more representative view than reading the head only,
    while still capping I/O on multi-MB files."""
    from pathlib import Path as _P
    p = _P(file_path)
    try:
        size = p.stat().st_size
    except FileNotFoundError:
        return ""
    if size <= total_bytes:
        return _safe_decode(p.read_bytes())
    chunk = total_bytes // 3
    with p.open("rb") as f:
        head = f.read(chunk)
        f.seek(max(0, size // 2 - chunk // 2))
        mid = f.read(chunk)
        f.seek(max(0, size - chunk))
        tail = f.read(chunk)
    # Join with paragraph breaks so the patterns that anchor on \n\n
    # still work at chunk boundaries.
    return "\n\n".join(s for s in (_safe_decode(head), _safe_decode(mid), _safe_decode(tail)) if s)


# ── Guess-format job (async, with progress) ──

@dataclass
class GuessJob:
    """Async format-matching job. The guess phase scans every built-in
    + custom pattern against the work's text and reports progress so
    the UI can show a real progress bar (replaces the old sync
    "猜测中…" spinner that froze for several seconds on large texts).
    """
    ref_id: str
    state: str = "running"  # running | done | error
    current_pattern: int = 0
    total_patterns: int = 0
    candidates: list[dict] = field(default_factory=list)
    suggested: Optional[str] = None
    text_len: int = 0
    scanned_len: int = 0
    error: Optional[str] = None
    started_at: float = 0.0
    ended_at: float = 0.0
    _task: Optional[asyncio.Task] = None

    def to_status(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "current_pattern": self.current_pattern,
            "total_patterns": self.total_patterns,
            "candidates": self.candidates,
            "suggested": self.suggested,
            "text_len": self.text_len,
            "scanned_len": self.scanned_len,
            "error": self.error,
        }


_guess_jobs: dict[str, GuessJob] = {}


def get_guess_job(ref_id: str) -> Optional[GuessJob]:
    return _guess_jobs.get(ref_id)


async def _run_guess(job: GuessJob, text: str, extra_patterns: list[dict] | None) -> None:
    """Body of the guess job. Scans patterns one by one, yielding to
    the loop between each so the UI's progress polls stay responsive
    and other endpoints don't starve. Caps scanned text length so very
    large works don't make the guess feel laggy — the actual detection
    job re-scans the FULL text with the chosen pattern."""
    from analysis.feature_extraction.chapter_parser import (
        _PATTERNS as BUILTIN, _compile_extra, _score_pattern,
    )
    try:
        job.state = "running"
        job.started_at = time.time()
        job.text_len = len(text)
        # Cap scan to ~2 MB — score-relevant signals (count / spacing /
        # short-line-fraction) saturate well before this on real novels.
        MAX_SCAN = 2_000_000
        scan_text = text[:MAX_SCAN] if len(text) > MAX_SCAN else text
        job.scanned_len = len(scan_text)
        custom = _compile_extra(extra_patterns)
        all_pats = list(BUILTIN) + custom
        custom_names = {n for n, _ in custom}
        job.total_patterns = len(all_pats)

        results: list[dict] = []
        for i, (name, pat) in enumerate(all_pats):
            job.current_pattern = i + 1
            # Run the regex pass in a worker thread so status polls
            # interleave with scanning. Each individual finditer on a
            # 2 MB text can take 50–200 ms (especially patterns with
            # lookbehinds); without this the event loop is blocked
            # for the whole pass and the progress bar stalls.
            ms = await asyncio.to_thread(lambda p=pat: list(p.finditer(scan_text)))
            score = _score_pattern(ms, len(scan_text))
            results.append({
                "name": name, "count": len(ms), "score": round(score, 3),
                "custom": name in custom_names,
            })

        results.sort(key=lambda c: -c["score"])
        job.candidates = results
        job.suggested = results[0]["name"] if results and results[0]["score"] >= 1.0 else None
        job.state = "done"
        job.ended_at = time.time()
    except Exception as e:
        logger.exception("[preprocess] guess job for %s failed", job.ref_id)
        job.state = "error"
        job.error = str(e)
        job.ended_at = time.time()


async def start_guess_job(ref_id: str, text: str,
                           extra_patterns: list[dict] | None = None) -> GuessJob:
    job = _guess_jobs.get(ref_id)
    if job and job.state == "running":
        return job
    job = GuessJob(ref_id=ref_id, state="running")
    _guess_jobs[ref_id] = job
    job._task = asyncio.create_task(_run_guess(job, text, extra_patterns))
    return job


async def start_guess_job_for_path(ref_id: str, file_path: str,
                                     extra_patterns: list[dict] | None = None) -> GuessJob:
    """Like ``start_guess_job`` but defers the file read to the worker so
    the calling endpoint returns immediately. Use this when the endpoint
    only has the file path (not the full text), to avoid blocking the
    event loop on a multi-MB read."""
    job = _guess_jobs.get(ref_id)
    if job and job.state == "running":
        return job
    job = GuessJob(ref_id=ref_id, state="running")
    _guess_jobs[ref_id] = job

    async def _wrapper():
        try:
            # Sample-read instead of full-file read: head + middle + tail
            # totaling ~2.5 MB. On a 50 MB work this saves seconds of I/O
            # without losing format-recognition quality — chapter markers
            # are consistent across the document.
            text = await asyncio.to_thread(read_sample, file_path, 2_500_000)
            await _run_guess(job, text, extra_patterns)
        except Exception as e:
            logger.exception("[preprocess] guess (lazy load) for %s failed", ref_id)
            job.state = "error"
            job.error = str(e)
            job.ended_at = time.time()
    job._task = asyncio.create_task(_wrapper())
    return job


def clear_guess_job(ref_id: str) -> None:
    _guess_jobs.pop(ref_id, None)



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
                          extra_patterns: list[dict] | None = None,
                          force_pattern: str | None = None,
                          force_patterns: list[str] | None = None) -> None:
    """Body of the preprocess job. Runs in its own task. Honors job._cancel
    and job._resume between chapters so the UI's pause/resume controls
    take effect at chapter boundaries (per user's chosen granularity)."""
    from analysis.feature_extraction.chapter_parser import (
        detect_chapters, flag_author_notes, flag_length_outliers,
        flag_garbled_chapters, make_preview, visible_char_count,
    )

    try:
        job.state = "running"
        if not job.started_at:
            job.started_at = time.time()
        job.phase = "matching"
        job.append_log("正在匹配章节格式…")

        # Phase 1: chapter detection (regex-heavy, can take 100ms+ per
        # pattern on multi-MB texts). Run in a worker thread so the UI
        # status poll stays responsive while patterns are scanning.
        result = await asyncio.to_thread(
            detect_chapters, text,
            extra_patterns=extra_patterns,
            force_pattern=force_pattern,
            force_patterns=force_patterns,
        )
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
        job.phase = "tagging"
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
            elif i % 50 == 0:
                # Yield only every 50 chapters — the per-chapter pause/
                # cancel check is already done at the top of the loop;
                # awaiting on every iteration added significant overhead
                # on 1000+ chapter works without buying any real
                # responsiveness.
                await asyncio.sleep(0)

        # Heuristic application + preview generation. These are all
        # synchronous Python operations; offload to a thread so the
        # status poll stays responsive even on works with thousands
        # of chapters or unusually long single chapters.
        job.phase = "finalizing"
        job.append_log("正在生成摘要与标记…")
        # Load user-managed keyword list once so the post-processing
        # heuristic respects the customized set.
        try:
            from pathlib import Path as _P
            import json as _json
            root = _P(__file__).resolve().parents[2]
            sp = root / "data" / "settings.json"
            settings_data = _json.loads(sp.read_text(encoding="utf-8")) if sp.exists() else {}
            extra_kw = settings_data.get("author_note_keywords") if isinstance(settings_data, dict) else None
        except Exception:
            extra_kw = None
        def _post_process():
            flag_author_notes(chapters, extra_keywords=extra_kw)
            flag_length_outliers(chapters)
            flag_garbled_chapters(chapters)
            for c in chapters:
                pv = make_preview(c.get("content") or "")
                c["preview_head"] = pv["head"]
                c["preview_tail"] = pv["tail"]
        await asyncio.to_thread(_post_process)
        flagged = [c for c in chapters if c.get("is_author_note")]
        outliers = [c for c in chapters if c.get("is_length_outlier")]
        job.flagged_count = len(flagged)
        job.append_log(
            f"完成。识别 {len(chapters)} 章，疑似作者题外话 {len(flagged)} 章，"
            f"长度异常 {len(outliers)} 章。"
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


async def start_job_for_path(ref_id: str, file_path: str,
                               per_chapter_delay_ms: int = 0,
                               extra_patterns: list[dict] | None = None,
                               force_pattern: str | None = None,
                               force_patterns: list[str] | None = None) -> "PreprocessJob":
    """Like ``start_job`` but defers the file read to the worker so the
    endpoint returns immediately. Use this for any 多-MB work where
    reading the file in the request handler would block the event loop
    and freeze unrelated requests for hundreds of ms."""
    job = _jobs.get(ref_id)
    if job and job.state in ("running", "paused"):
        return job
    job = PreprocessJob(ref_id=ref_id)
    job._resume = threading.Event()
    job._resume.set()
    job.state = "running"
    _jobs[ref_id] = job

    async def _wrapper():
        try:
            from pathlib import Path as _P
            job.phase = "loading"
            job.append_log("正在读取正文文件…")
            text = await asyncio.to_thread(_P(file_path).read_text, encoding="utf-8")
            job.append_log(f"已读取 {len(text):,} 字符")
            await _run_detection(job, text, per_chapter_delay_ms,
                                  extra_patterns, force_pattern, force_patterns)
        except Exception as e:
            logger.exception("[preprocess] detection (lazy load) for %s failed", ref_id)
            job.state = "error"
            job.error = str(e)
            job.ended_at = time.time()
    job._task = asyncio.create_task(_wrapper())
    return job


async def start_job(ref_id: str, text: str,
                     per_chapter_delay_ms: int = 0,
                     extra_patterns: list[dict] | None = None,
                     force_pattern: str | None = None,
                     force_patterns: list[str] | None = None) -> PreprocessJob:
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
        _run_detection(job, text, per_chapter_delay_ms,
                        extra_patterns, force_pattern, force_patterns),
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
    from analysis.feature_extraction.chapter_parser import visible_char_count
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
            "chapter_id", "display_number",
            "number", "parsed_number", "title", "title_only", "raw_marker",
            "pattern", "volume",
            "is_author_note", "author_note_score", "author_note_reasons",
            "is_length_outlier", "outlier_kind",
            "is_split_piece", "is_edited", "had_asides_removed",
            "is_garbled", "garbled_reasons",
            "preview_head", "preview_tail",
            "content_start", "content_end",
        )} | {"char_count": visible_char_count(c.get("content") or "")}
        for c in chapters
    ]
    from analysis.feature_extraction.chapter_parser import find_chapter_gaps
    state["preprocess"] = {
        "chapters": light,
        "total_chapters": len(light),
        "flagged_count": sum(1 for c in light if c.get("is_author_note")),
        "gaps": find_chapter_gaps(chapters),
    }
    rdb.update_work(ref_id, segments_json=json.dumps(state, ensure_ascii=False))
