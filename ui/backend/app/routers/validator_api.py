"""Consistency validator API (spec § 模块 9).

- ``GET  /api/validator/quick?project_id=X`` — L1 + L2 only, sync,
  zero-token; returns issues immediately.
- ``POST /api/validator/run-full-check`` — L1+L2+L3 async; spawns a
  background task, returns the validator_job_id; clients poll via
  ``GET /api/validator/jobs/{job_id}``.
"""
from __future__ import annotations

import asyncio
import logging
import threading
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from ..services.commit_pipeline import validator
from ..services.project_paths import get_db_path

logger = logging.getLogger("inkoctobot.routers.validator_api")


router = APIRouter(prefix="/api/validator", tags=["validator"])


# In-memory job registry — validator runs are sufficiently transient
# that we don't need a DB table (cf. commit_tasks). Process restart
# clears history; clients should consume results promptly.
_jobs: dict[str, dict[str, Any]] = {}
_lock = threading.Lock()


@router.get("/quick")
def quick_check(project_id: str = Query(...)) -> dict:
    """L1 + L2 synchronous check. Returns categorized issue list."""
    issues = validator.run_all_sync(get_db_path(), project_id)
    return {
        "project_id": project_id,
        "issues":     [i.__dict__ for i in issues],
        "count":      len(issues),
    }


@router.post("/run-full-check")
def run_full_check(project_id: str = Query(...)) -> dict:
    """Spawn an async L1+L2+L3 check. Returns job_id for polling."""
    job_id = f"vj_{uuid.uuid4().hex[:12]}"
    with _lock:
        _jobs[job_id] = {
            "job_id":     job_id,
            "project_id": project_id,
            "state":      "running",
            "result":     None,
        }
    db = get_db_path()

    async def _runner() -> None:
        try:
            result = await validator.run_all_async(
                db, project_id, include_llm=True,
            )
            with _lock:
                _jobs[job_id]["result"] = result
                _jobs[job_id]["state"] = "completed"
        except Exception as e:
            with _lock:
                _jobs[job_id]["state"] = "failed"
                _jobs[job_id]["error"] = str(e)

    # Run on the existing loop if available; otherwise in a daemon thread.
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_runner(), name=f"validator:{job_id}")
    except RuntimeError:
        threading.Thread(
            target=lambda: asyncio.run(_runner()),
            name=f"validator-{job_id}", daemon=True,
        ).start()
    return {"job_id": job_id, "state": "running"}


@router.get("/jobs/{job_id}")
def get_job(job_id: str) -> dict:
    with _lock:
        job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(404, f"job {job_id!r} not found")
    return job
