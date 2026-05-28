"""HTTP API for the market extractor (Phase 6).

Endpoints (spec § 任务 6.4):
    POST   /api/market-extractor/jobs
    GET    /api/market-extractor/jobs
    GET    /api/market-extractor/jobs/:id
    GET    /api/market-extractor/representative-works
    POST   /api/market-extractor/works/:id/exclude
    GET    /api/market-extractor/chapter-features/:work_id
    GET    /api/market-extractor/neologisms/:work_id
    GET    /api/market-extractor/genre-dictionary/:category
    GET    /api/platform-profiles
    GET    /api/platform-profiles/current?platform=X&category=Y
    GET    /api/platform-profiles/:id
"""
from __future__ import annotations

import json
import logging
import sqlite3

from fastapi import APIRouter, Body, HTTPException, Query

from ..services.project_paths import get_db_path
from ..services.market_extractor import (
    dictionaries, job_runner, representative_selector,
)

logger = logging.getLogger("inkoctobot.routers.market_extractor_api")


router = APIRouter(prefix="/api/market-extractor", tags=["market-extractor"])
profiles_router = APIRouter(prefix="/api/platform-profiles", tags=["platform-profiles"])


# ─────────── jobs ───────────


@router.post("/jobs")
def create_job(body: dict = Body(...)) -> dict:
    platform = (body.get("platform") or "").strip()
    category = (body.get("category") or "").strip()
    crawler_db = (body.get("crawler_db") or "").strip() or None
    if not platform or not category:
        raise HTTPException(400, "platform + category required")
    job_id = job_runner.run_job_in_background(
        get_db_path(), platform, category, crawler_db=crawler_db,
    )
    return {"job_id": job_id, "state": "queued"}


@router.get("/jobs")
def list_jobs(limit: int = Query(default=50, le=200)) -> dict:
    return {"jobs": job_runner.list_jobs(get_db_path(), limit=limit)}


@router.get("/jobs/{job_id}")
def get_job(job_id: str) -> dict:
    job = job_runner.get_job_status(get_db_path(), job_id)
    if job is None:
        raise HTTPException(404, f"job {job_id!r} not found")
    return job


# ─────────── representative works ───────────


@router.get("/representative-works")
def list_works(
    platform: str = Query(...),
    category: str = Query(...),
    include_holdout: bool = Query(default=False),
) -> dict:
    works = representative_selector.list_selected(
        get_db_path(), platform, category,
        include_holdout=include_holdout,
    )
    return {"platform": platform, "category": category, "works": works}


@router.post("/works/{work_id}/exclude")
def exclude_work(work_id: str) -> dict:
    ok = representative_selector.exclude_work(get_db_path(), work_id)
    if not ok:
        raise HTTPException(404, f"work {work_id!r} not found")
    return {"ok": True, "work_id": work_id, "selected_for_extraction": False}


# ─────────── inspect ───────────


@router.get("/chapter-features/{work_id}")
def list_chapter_features(work_id: str) -> dict:
    with sqlite3.connect(get_db_path()) as con:
        con.row_factory = sqlite3.Row
        rows = [dict(r) for r in con.execute(
            "SELECT * FROM chapter_features WHERE work_id = ? "
            "ORDER BY chapter_num",
            (work_id,),
        ).fetchall()]
    return {"work_id": work_id, "chapters": rows}


@router.get("/neologisms/{work_id}")
def list_neologisms(work_id: str) -> dict:
    with sqlite3.connect(get_db_path()) as con:
        con.row_factory = sqlite3.Row
        rows = [dict(r) for r in con.execute(
            "SELECT * FROM work_neologisms WHERE work_id = ? "
            "ORDER BY frequency_in_5_chapters DESC",
            (work_id,),
        ).fetchall()]
    return {"work_id": work_id, "neologisms": rows}


@router.get("/genre-dictionary/{category}")
def get_genre_dict(category: str) -> dict:
    words = sorted(dictionaries.load_genre_dict(category))
    return {"category": category, "word_count": len(words), "words": words}


# ─────────── platform profiles ───────────


@profiles_router.get("")
def list_profiles(
    platform: str | None = Query(default=None),
    category: str | None = Query(default=None),
) -> dict:
    where = []
    params: list = []
    if platform:
        where.append("platform = ?")
        params.append(platform)
    if category:
        where.append("category = ?")
        params.append(category)
    sql = "SELECT * FROM platform_profiles"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY valid_from DESC"
    with sqlite3.connect(get_db_path()) as con:
        con.row_factory = sqlite3.Row
        rows = [dict(r) for r in con.execute(sql, params).fetchall()]
    return {"profiles": rows}


@profiles_router.get("/current")
def get_current_profile(
    platform: str = Query(...),
    category: str = Query(...),
) -> dict:
    with sqlite3.connect(get_db_path()) as con:
        con.row_factory = sqlite3.Row
        row = con.execute(
            "SELECT * FROM platform_profiles "
            "WHERE platform = ? AND category = ? "
            "AND superseded_by_profile_id IS NULL "
            "ORDER BY profile_version DESC LIMIT 1",
            (platform, category),
        ).fetchone()
    if row is None:
        raise HTTPException(404, "no active profile for that platform/category")
    return dict(row)


@profiles_router.get("/{profile_id}")
def get_profile(profile_id: str) -> dict:
    with sqlite3.connect(get_db_path()) as con:
        con.row_factory = sqlite3.Row
        row = con.execute(
            "SELECT * FROM platform_profiles WHERE profile_id = ?",
            (profile_id,),
        ).fetchone()
    if row is None:
        raise HTTPException(404, f"profile {profile_id!r} not found")
    return dict(row)
