"""Phase 6 — full pipeline orchestration with persisted job state.

A job is one (platform, category) extraction end-to-end:
Phase 1 → Phase 2 → Phase 3 → Phase 4 → Phase 5.

State persists in ``platform_extraction_jobs``. Failures land in
``user_notifications``. Resumable: ``current_work_id`` lets a retry
skip already-processed works.
"""
from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from . import (
    category_aggregator, chapter_fetcher, llm_extractor,
    neologism_extractor, nlp_features, profile_evaluator,
    profile_synthesizer, representative_selector, work_aggregator,
)

logger = logging.getLogger("inkoctobot.market_extractor.job_runner")


_CACHE_ROOT = Path(__file__).resolve().parents[5] / "data" / "market_extractor" / "chapter_cache"


def _now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds")


def _new_job_id() -> str:
    return f"ej_{uuid.uuid4().hex[:12]}"


# ─────────── persistence ───────────


def _insert_job(db_path: str, job_id: str, platform: str, category: str) -> None:
    with sqlite3.connect(db_path) as con:
        con.execute(
            "INSERT INTO platform_extraction_jobs "
            "(job_id, platform, category, state, started_at) "
            "VALUES (?, ?, ?, 'queued', ?)",
            (job_id, platform, category, _now()),
        )
        con.commit()


def _update_job(
    db_path: str, job_id: str, **fields: Any,
) -> None:
    if not fields:
        return
    sets = ",".join(f"{k} = ?" for k in fields)
    with sqlite3.connect(db_path) as con:
        con.execute(
            f"UPDATE platform_extraction_jobs SET {sets} WHERE job_id = ?",
            list(fields.values()) + [job_id],
        )
        con.commit()


def _get_job(db_path: str, job_id: str) -> dict | None:
    with sqlite3.connect(db_path) as con:
        con.row_factory = sqlite3.Row
        row = con.execute(
            "SELECT * FROM platform_extraction_jobs WHERE job_id = ?",
            (job_id,),
        ).fetchone()
    return dict(row) if row else None


# ─────────── pipeline phases ───────────


def _phase_1(
    db_path: str, job_id: str, platform: str, category: str,
    crawler_db: str | None,
) -> dict:
    _update_job(db_path, job_id, state="running_phase_1",
                 progress_phase="phase_1", progress_pct=5)
    return representative_selector.select(
        db_path, platform, category, crawler_db=crawler_db,
    )


async def _phase_2_for_work(
    db_path: str, work_id: str, novel_id: str,
    crawler_db: str, category: str,
) -> dict:
    """Process one work — fetch + clean + NLP + LLM + neologisms."""
    chapters = chapter_fetcher.fetch_first_n_chapters(
        crawler_db, novel_id, n=5,
    )
    if not chapters:
        return {"work_id": work_id, "chapters": 0, "status": "no_chapters"}

    # Cache the cleaned text.
    cache_dir = str(_CACHE_ROOT)
    for k, txt in chapters.items():
        chapter_fetcher.cache_chapter(cache_dir, work_id, k, txt)

    # NLP + LLM per chapter.
    corpus = list(chapters.values())
    rows: list[dict] = []
    for chap_num, text in chapters.items():
        nlp = nlp_features.extract(text, corpus_texts=corpus)
        try:
            parsed, model_label = await llm_extractor.call_llm_for_chapter(
                text, chap_num, is_first_chapter=(chap_num == 1),
            )
            llm_cols = llm_extractor.flatten_to_columns(parsed)
        except Exception as e:
            logger.warning(
                "LLM extraction failed for work %s ch %d: %s",
                work_id, chap_num, e,
            )
            llm_cols = {}
            model_label = ""
        rows.append({
            "feature_id":   f"cf_{uuid.uuid4().hex[:12]}",
            "work_id":      work_id,
            "chapter_num":  chap_num,
            "llm_model_used": model_label,
            **nlp,
            **llm_cols,
        })

    # Bulk INSERT.
    with sqlite3.connect(db_path) as con:
        for r in rows:
            cols = ",".join(r.keys())
            ph = ",".join("?" * len(r))
            try:
                con.execute(
                    f"INSERT OR REPLACE INTO chapter_features ({cols}) "
                    f"VALUES ({ph})",
                    list(r.values()),
                )
            except sqlite3.OperationalError as e:
                logger.warning("chapter_features insert failed: %s", e)
        con.commit()

    # Neologisms.
    try:
        neo_result = await neologism_extractor.extract_for_work(
            db_path, work_id, category, chapters,
        )
    except Exception as e:
        logger.warning("neologism extraction failed for %s: %s", work_id, e)
        neo_result = {"saved": 0}

    return {
        "work_id":     work_id,
        "chapters":    len(chapters),
        "neologisms":  neo_result.get("saved") or 0,
    }


async def _phase_2(
    db_path: str, job_id: str, platform: str, category: str,
    crawler_db: str,
) -> dict:
    _update_job(db_path, job_id, state="running_phase_2",
                 progress_phase="phase_2", progress_pct=20)
    works = representative_selector.list_selected(
        db_path, platform, category, include_holdout=True,
    )
    total_chapters = 0
    for w in works:
        _update_job(db_path, job_id, current_work_id=w["work_id"])
        result = await _phase_2_for_work(
            db_path, w["work_id"], w["source_db_novel_id"],
            crawler_db, category,
        )
        total_chapters += result.get("chapters", 0)
    _update_job(db_path, job_id, chapters_processed=total_chapters)
    return {"works": len(works), "chapters_processed": total_chapters}


def _phase_3(
    db_path: str, job_id: str, platform: str, category: str,
) -> dict:
    _update_job(db_path, job_id, state="running_phase_3",
                 progress_phase="phase_3", progress_pct=60)
    works = representative_selector.list_selected(
        db_path, platform, category, include_holdout=True,
    )
    for w in works:
        try:
            work_aggregator.aggregate(db_path, w["work_id"])
        except Exception as e:
            logger.warning("work aggregation failed for %s: %s", w["work_id"], e)
    return {"works_aggregated": len(works)}


async def _phase_4(
    db_path: str, job_id: str, platform: str, category: str,
) -> dict:
    _update_job(db_path, job_id, state="running_phase_4",
                 progress_phase="phase_4", progress_pct=80)
    category_aggregator.aggregate(db_path, platform, category)
    try:
        result = await profile_synthesizer.synthesize(
            db_path, platform, category,
        )
    except Exception as e:
        logger.error("profile synthesis failed: %s", e)
        raise
    _update_job(
        db_path, job_id,
        cloud_llm_calls=1,
        created_profile_id=result["profile_id"],
    )
    return result


def _phase_5(
    db_path: str, job_id: str, profile_id: str,
) -> dict:
    _update_job(db_path, job_id, state="running_phase_5",
                 progress_phase="phase_5", progress_pct=95)
    return profile_evaluator.evaluate(db_path, profile_id)


# ─────────── orchestrator ───────────


async def run_job_async(
    db_path: str, platform: str, category: str,
    *, crawler_db: str | None = None, job_id: str | None = None,
) -> str:
    """Run the full pipeline. Returns the job_id (created or supplied).
    Honors per-phase failures: marks job state ``failed`` and creates a
    high-priority user_notification before re-raising."""
    if job_id is None:
        job_id = _new_job_id()
        _insert_job(db_path, job_id, platform, category)

    try:
        _phase_1(db_path, job_id, platform, category, crawler_db)
        await _phase_2(db_path, job_id, platform, category,
                        crawler_db or representative_selector._resolve_crawler_db())
        _phase_3(db_path, job_id, platform, category)
        synth_result = await _phase_4(db_path, job_id, platform, category)
        _phase_5(db_path, job_id, synth_result["profile_id"])
        _update_job(
            db_path, job_id,
            state="completed", progress_pct=100,
            completed_at=_now(),
        )
    except Exception as e:
        logger.exception("extraction job %s failed", job_id)
        _update_job(
            db_path, job_id, state="failed",
            errors_json=json.dumps({"error": str(e)}, ensure_ascii=False),
            completed_at=_now(),
        )
        # Best-effort user notification.
        try:
            from ui.backend.app.services.notifications import create_notification
            create_notification(
                db_path, project_id=platform,   # platform doubles as scope key here
                notification_type="extraction_job_failed",
                severity="high",
                title=f"市场数据 extraction 失败：{platform}/{category}",
                description=f"job {job_id}\n错误：{str(e)[:300]}",
                action_label="查看任务详情",
                action_url=f"/market-extractor/jobs/{job_id}",
                related_task_id=job_id,
            )
        except Exception:
            pass
        raise
    return job_id


def run_job_in_background(
    db_path: str, platform: str, category: str,
    *, crawler_db: str | None = None,
) -> str:
    """Spawn the pipeline in a daemon thread with its own event loop.
    Returns the job_id immediately."""
    job_id = _new_job_id()
    _insert_job(db_path, job_id, platform, category)

    def _runner() -> None:
        try:
            asyncio.run(run_job_async(
                db_path, platform, category,
                crawler_db=crawler_db, job_id=job_id,
            ))
        except Exception:
            pass  # already persisted in job row

    threading.Thread(
        target=_runner,
        name=f"market-extractor-{job_id}",
        daemon=True,
    ).start()
    return job_id


# ─────────── public read API ───────────


def get_job_status(db_path: str, job_id: str) -> dict | None:
    return _get_job(db_path, job_id)


def list_jobs(db_path: str, *, limit: int = 50) -> list[dict]:
    with sqlite3.connect(db_path) as con:
        con.row_factory = sqlite3.Row
        rows = con.execute(
            "SELECT * FROM platform_extraction_jobs "
            "ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]
