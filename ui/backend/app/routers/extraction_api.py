"""
/api/extraction — Novel corpus ingestion & skill extraction pipeline API.

Exposes the novel_ingester + skill extraction orchestrator to the frontend.
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query, UploadFile, File
from pydantic import BaseModel

from ..settings import settings
from ..utils import load_repo_config, get_db_path

router = APIRouter(prefix="/extraction", tags=["extraction"])
logger = logging.getLogger("inkoctobot.ui.backend.extraction_api")

# ── Pipeline status tracking (in-memory, per-process) ──
_pipeline_status: dict[str, Any] = {"status": "idle"}
_pipeline_lock = threading.Lock()


def _get_db_path() -> str:
    if settings.test_mode and settings.data_dir:
        return str(settings.data_dir / "novels.db")
    try:
        repo_cfg = load_repo_config(settings.repo_root)
        return get_db_path(repo_cfg, settings.repo_root)
    except FileNotFoundError:
        return str(settings.repo_root / "data" / "novels.db")


def _corpus_dir() -> Path:
    return settings.repo_root / "data" / "novel_corpus"


# ═══ Novel Corpus — Ingest ═══════════════════════════════

@router.post("/ingest")
def ingest_corpus():
    """Ingest all novels from corpus directory (批量导入小说)."""
    try:
        from preprocessing.novel_ingester import NovelIngester

        db_path = _get_db_path()
        corpus = _corpus_dir()
        if not corpus.exists():
            raise HTTPException(400, f"语料库目录不存在: {corpus}")

        ingester = NovelIngester(db_path, corpus)
        results = ingester.ingest_all()

        summaries = []
        total_needs_review = 0
        for r in results:
            summaries.append({
                "title": r.title,
                "author": r.author,
                "chapters": r.total_chapters,
                "chars": r.total_chars,
                "excluded_notes": r.excluded_author_notes,
                "needs_review": len(r.needs_review),
            })
            total_needs_review += len(r.needs_review)

        return {
            "ingested": len(results),
            "novels": summaries,
            "needs_review": total_needs_review,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Ingest failed")
        raise HTTPException(500, f"导入失败: {e}")


@router.post("/ingest/upload")
async def upload_novel(file: UploadFile = File(...)):
    """Upload a single novel txt file to the corpus directory."""
    try:
        corpus = _corpus_dir()
        corpus.mkdir(parents=True, exist_ok=True)
        dest = corpus / (file.filename or "upload.txt")
        content = await file.read()
        dest.write_bytes(content)

        # Ingest the single file
        from preprocessing.novel_ingester import NovelIngester

        db_path = _get_db_path()
        ingester = NovelIngester(db_path, corpus)
        result = ingester.ingest_single(str(dest))

        return {
            "title": result.title,
            "author": result.author,
            "chapters": result.total_chapters,
            "chars": result.total_chars,
            "excluded_notes": result.excluded_author_notes,
            "needs_review": len(result.needs_review),
            "file": str(dest),
        }
    except Exception as e:
        logger.exception("Upload ingest failed")
        raise HTTPException(500, f"上传导入失败: {e}")


# ═══ Novel Metadata ══════════════════════════════════════

@router.get("/novels")
def list_novels(
    search: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    """List ingested novels with metadata."""
    import sqlite3

    db_path = _get_db_path()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    conditions = []
    params: list[Any] = []

    if search:
        conditions.append("(title LIKE ? OR author LIKE ?)")
        params.extend([f"%{search}%", f"%{search}%"])
    if status:
        conditions.append("status = ?")
        params.append(status)

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

    total = conn.execute(
        f"SELECT COUNT(*) FROM novel_metadata {where}", params
    ).fetchone()[0]

    rows = conn.execute(
        f"SELECT * FROM novel_metadata {where} ORDER BY title LIMIT ? OFFSET ?",
        params + [limit, offset],
    ).fetchall()
    conn.close()

    return {
        "items": [dict(r) for r in rows],
        "total": total,
    }


@router.get("/novels/{ref_id}")
def get_novel(ref_id: str):
    """Get detailed novel metadata and chapter list."""
    import sqlite3

    db_path = _get_db_path()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    novel = conn.execute(
        "SELECT * FROM novel_metadata WHERE ref_id = ?", (ref_id,)
    ).fetchone()
    if not novel:
        conn.close()
        raise HTTPException(404, "小说不存在")

    chapters = conn.execute(
        "SELECT chapter_num, original_title, char_count, is_author_note "
        "FROM novel_chapters WHERE ref_id = ? ORDER BY chapter_num",
        (ref_id,),
    ).fetchall()
    conn.close()

    return {
        "novel": dict(novel),
        "chapters": [dict(c) for c in chapters],
    }


# ═══ Extraction Pipeline — Run ═══════════════════════════

class PipelineRunRequest(BaseModel):
    phases: Optional[list[str]] = None
    novels: Optional[list[str]] = None
    resume: bool = True


@router.post("/run")
async def run_pipeline(req: PipelineRunRequest):
    """Start the extraction pipeline (async background task)."""
    global _pipeline_status
    with _pipeline_lock:
        if _pipeline_status.get("status") == "running":
            raise HTTPException(409, "提取流程已在运行中")
        _pipeline_status = {
            "status": "running",
            "phases": req.phases or ["clean", "chapter", "novel", "pattern", "emit"],
            "progress": "初始化...",
            "error": None,
        }

    asyncio.create_task(_run_pipeline_bg(req))
    return {"status": "started", "phases": req.phases}


async def _run_pipeline_bg(req: PipelineRunRequest):
    global _pipeline_status
    try:
        from preprocessing.skill_extraction.orchestrator import SkillExtractionOrchestrator
        from models.router import ModelRouter

        db_path = _get_db_path()
        corpus = _corpus_dir()

        orchestrator = SkillExtractionOrchestrator(
            db_path=db_path,
            corpus_dir=str(corpus),
            model_router=ModelRouter(),
        )

        _pipeline_status["progress"] = "运行中..."
        result = await orchestrator.run(
            resume=req.resume,
            novels=req.novels,
            phases=req.phases,
        )

        _pipeline_status = {
            "status": "done",
            "result": result,
            "progress": "完成",
            "error": None,
        }

    except Exception as e:
        logger.exception("Pipeline failed")
        _pipeline_status = {
            "status": "error",
            "error": str(e)[:500],
            "progress": "失败",
        }


@router.get("/run/status")
def pipeline_status():
    """Get current pipeline execution status."""
    return _pipeline_status


# ═══ Extraction Progress ═════════════════════════════════

@router.get("/progress")
def get_extraction_progress():
    """Get overall extraction progress across all novels and phases."""
    import sqlite3

    db_path = _get_db_path()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    try:
        total_novels = conn.execute(
            "SELECT COUNT(*) FROM novel_metadata"
        ).fetchone()[0]
    except Exception:
        return {"total_novels": 0, "phases": {}, "patterns": {}, "skills_emitted": 0}

    phase_status: dict[str, dict] = {}
    for phase in ("clean", "chapter_extract", "novel_aggregate", "pattern_mine"):
        rows = conn.execute(
            "SELECT status, COUNT(*) as cnt FROM extraction_progress "
            "WHERE phase = ? GROUP BY status",
            (phase,),
        ).fetchall()
        phase_status[phase] = {r["status"]: r["cnt"] for r in rows}

    try:
        pattern_counts = conn.execute(
            "SELECT category, COUNT(*) as cnt FROM extracted_patterns "
            "GROUP BY category"
        ).fetchall()
        patterns = {r["category"]: r["cnt"] for r in pattern_counts}
    except Exception:
        patterns = {}

    try:
        emitted = conn.execute(
            "SELECT COUNT(*) FROM extracted_patterns WHERE skill_emitted = 1"
        ).fetchone()[0]
    except Exception:
        emitted = 0

    conn.close()

    return {
        "total_novels": total_novels,
        "phases": phase_status,
        "patterns": patterns,
        "skills_emitted": emitted,
    }


# ═══ Patterns ════════════════════════════════════════════

@router.get("/patterns")
def list_patterns(
    category: Optional[str] = None,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    """List extracted patterns."""
    import sqlite3

    db_path = _get_db_path()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    conditions = []
    params: list[Any] = []
    if category:
        conditions.append("category = ?")
        params.append(category)

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

    try:
        total = conn.execute(
            f"SELECT COUNT(*) FROM extracted_patterns {where}", params
        ).fetchone()[0]
        rows = conn.execute(
            f"SELECT * FROM extracted_patterns {where} "
            "ORDER BY quality_score DESC LIMIT ? OFFSET ?",
            params + [limit, offset],
        ).fetchall()
    except Exception:
        conn.close()
        return {"items": [], "total": 0}

    conn.close()

    items = []
    for r in rows:
        d = dict(r)
        # Parse examples JSON for frontend
        if d.get("examples_json"):
            try:
                d["examples"] = json.loads(d["examples_json"])
            except Exception:
                d["examples"] = []
        else:
            d["examples"] = []
        items.append(d)

    return {"items": items, "total": total}


# ═══ Skill Emission ══════════════════════════════════════

@router.post("/emit")
def emit_skills(category: Optional[str] = None):
    """Generate skill files from mined patterns."""
    try:
        from preprocessing.skill_extraction.skill_emitter import SkillEmitter

        db_path = _get_db_path()
        emitter = SkillEmitter(db_path)

        if category:
            count = emitter.emit_category(category)
            return {"category": category, "skills_emitted": count}
        else:
            counts = emitter.emit_all()
            return {"skills_emitted": counts}
    except Exception as e:
        logger.exception("Emit failed")
        raise HTTPException(500, f"Skill生成失败: {e}")


# ═══ Clean Status ════════════════════════════════════════

@router.get("/clean-status")
def clean_status():
    """Get cleaning report for ingested novels."""
    import sqlite3

    db_path = _get_db_path()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    try:
        novels = conn.execute(
            "SELECT ref_id, title, author, status, total_chapters, total_chars, "
            "excluded_author_notes FROM novel_metadata ORDER BY title"
        ).fetchall()
    except Exception:
        conn.close()
        return {"novels": [], "total": 0, "total_chapters": 0, "total_chars": 0}

    conn.close()

    total_chapters = sum(r["total_chapters"] or 0 for r in novels)
    total_chars = sum(r["total_chars"] or 0 for r in novels)
    total_notes = sum(r["excluded_author_notes"] or 0 for r in novels)

    return {
        "novels": [dict(r) for r in novels],
        "total": len(novels),
        "total_chapters": total_chapters,
        "total_chars": total_chars,
        "total_author_notes_removed": total_notes,
    }
