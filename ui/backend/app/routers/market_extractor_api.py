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


@router.post("/jobs/{job_id}/cancel")
def cancel_job_endpoint(job_id: str) -> dict:
    """Cooperative cancel: state flipped to 'cancelled' so the running
    pipeline bails at the next phase checkpoint."""
    result = job_runner.cancel_job(get_db_path(), job_id)
    if result is None:
        raise HTTPException(404, f"job {job_id!r} not found")
    return result


@router.get("/platforms")
def list_platforms() -> dict:
    """Surface platforms the user actually has in the crawler DB."""
    import os as _os, sqlite3 as _sqlite3
    crawler_db_path = _os.path.join(
        _os.path.dirname(get_db_path()), "InkOctoBot_Crawler.db",
    )
    if not _os.path.exists(crawler_db_path):
        return {"platforms": [], "warning": "crawler DB not configured"}
    try:
        with _sqlite3.connect(crawler_db_path) as con:
            con.row_factory = _sqlite3.Row
            rows = con.execute(
                "SELECT platform, COUNT(*) AS book_count "
                "FROM novels GROUP BY platform "
                "ORDER BY book_count DESC"
            ).fetchall()
        return {"platforms": [
            {"key": r["platform"], "label": r["platform"], "book_count": r["book_count"]}
            for r in rows if r["platform"]
        ]}
    except _sqlite3.OperationalError as e:
        return {"platforms": [], "warning": f"crawler db read failed: {e}"}


@router.get("/aggregated-stats")
def get_aggregated_stats(platform: str = Query(...), category: str = Query(...)) -> dict:
    """Return the latest ``category_aggregated_stats`` row (or empty
    fields when none). This is the data the prompt's
    ``platform_market`` / 'market overview' loaders read at generation
    time — exposing it here lets the UI show users exactly what would
    be injected into a chapter prompt."""
    import sqlite3 as _sqlite3
    try:
        with _sqlite3.connect(get_db_path()) as con:
            con.row_factory = _sqlite3.Row
            row = con.execute(
                "SELECT * FROM category_aggregated_stats "
                "WHERE platform = ? AND category = ? "
                "ORDER BY aggregated_at DESC LIMIT 1",
                (platform, category),
            ).fetchone()
    except _sqlite3.OperationalError:
        return {"platform": platform, "category": category, "stats": None}
    return {
        "platform": platform,
        "category": category,
        "stats": dict(row) if row else None,
    }


@router.get("/categories")
def list_categories(platform: str = Query("")) -> dict:
    """Surface 榜单 categories from the crawler DB."""
    import os as _os, sqlite3 as _sqlite3
    crawler_db_path = _os.path.join(
        _os.path.dirname(get_db_path()), "InkOctoBot_Crawler.db",
    )
    if not _os.path.exists(crawler_db_path):
        return {"categories": [], "warning": "crawler DB not configured"}
    try:
        with _sqlite3.connect(crawler_db_path) as con:
            con.row_factory = _sqlite3.Row
            if platform:
                rows = con.execute(
                    "SELECT rank_family, rank_sub_cat, COUNT(*) AS list_count "
                    "FROM rank_lists WHERE platform = ? "
                    "GROUP BY rank_family, rank_sub_cat "
                    "ORDER BY list_count DESC",
                    (platform,),
                ).fetchall()
            else:
                rows = con.execute(
                    "SELECT rank_family, rank_sub_cat, COUNT(*) AS list_count "
                    "FROM rank_lists "
                    "GROUP BY rank_family, rank_sub_cat "
                    "ORDER BY list_count DESC"
                ).fetchall()
        out = []
        for r in rows:
            fam = r["rank_family"] or "未知"
            sub = r["rank_sub_cat"] or ""
            label = f"{fam} · {sub}" if sub else fam
            key = sub or fam
            out.append({"key": key, "label": label,
                        "rank_family": fam, "rank_sub_cat": sub,
                        "list_count": r["list_count"]})
        return {"categories": out}
    except _sqlite3.OperationalError as e:
        return {"categories": [], "warning": f"crawler db read failed: {e}"}


@router.post("/manual-prompt")
def build_manual_prompt(body: dict = Body(...)) -> dict:
    """Assemble the LLM prompt for manual-mode usage. The user copies
    the returned prompt into a browser LLM, pastes the response back
    via /manual-submit."""
    platform = (body.get("platform") or "").strip()
    category = (body.get("category") or "").strip()
    if not platform or not category:
        raise HTTPException(400, "platform + category required")
    works = representative_selector.list_selected(
        get_db_path(), platform, category, include_holdout=False,
    )
    work_titles = [
        (w.get("title") or w.get("source_db_novel_id") or w.get("work_id"))
        for w in works[:10]
    ]
    prompt = (
        f"请基于以下 ({platform} × {category}) 平台代表作，"
        f"总结一份「平台风格基线 (platform directive)」。\n\n"
        f"代表作清单 (top {len(work_titles)}):\n"
        + "\n".join(f"  - {t}" for t in work_titles)
        + "\n\n请输出 JSON：\n"
        '{\n'
        '  "profile_summary": "...",\n'
        '  "style_baseline": "...",\n'
        '  "signature_devices_description": "...",\n'
        '  "pacing_guidance": "...",\n'
        '  "recommended_openings": ["...", "..."]\n'
        '}\n'
    )
    return {"prompt": prompt, "platform": platform, "category": category,
            "work_count": len(works)}


@router.post("/manual-submit")
def submit_manual_extraction(body: dict = Body(...)) -> dict:
    """Persist a manual-mode response as a new platform_profile row."""
    import uuid as _uuid, json as _json, sqlite3 as _sqlite3
    platform = (body.get("platform") or "").strip()
    category = (body.get("category") or "").strip()
    raw = (body.get("response_raw") or "").strip()
    if not platform or not category or not raw:
        raise HTTPException(400, "platform + category + response_raw required")
    parsed: dict = {}
    try:
        t = raw
        if t.startswith("```"):
            t = t.lstrip("`")
            if t.lower().startswith("json"):
                t = t[4:]
            t = t.strip()
            if t.endswith("```"):
                t = t[:-3].strip()
        parsed = _json.loads(t)
    except Exception:
        i, j = raw.find("{"), raw.rfind("}")
        if i >= 0 and j > i:
            try:
                parsed = _json.loads(raw[i:j + 1])
            except Exception:
                parsed = {}
    profile_id = f"pp_manual_{_uuid.uuid4().hex[:10]}"
    with _sqlite3.connect(get_db_path()) as con:
        con.execute(
            """INSERT INTO platform_profiles
               (profile_id, platform, category, profile_version,
                profile_summary, style_baseline, signature_devices_description,
                pacing_guidance, recommended_openings_json,
                loader_payload, confidence_label,
                extraction_started_at, extraction_completed_at)
               VALUES (?, ?, ?, 1, ?, ?, ?, ?, ?, ?, 'manual',
                       CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)""",
            (
                profile_id, platform, category,
                parsed.get("profile_summary", ""),
                parsed.get("style_baseline", ""),
                parsed.get("signature_devices_description", ""),
                parsed.get("pacing_guidance", ""),
                _json.dumps(parsed.get("recommended_openings", []),
                            ensure_ascii=False),
                raw,
            ),
        )
        con.commit()
    return {"profile_id": profile_id, "platform": platform,
            "category": category, "parsed_keys": list(parsed.keys())}


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
