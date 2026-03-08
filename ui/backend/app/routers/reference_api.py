"""
/api/references — 参考作品库 CRUD + 预处理触发 + 条目管理
"""
from __future__ import annotations
import json
from pathlib import Path
from fastapi import APIRouter, HTTPException, Query, UploadFile, File, Form
from pydantic import BaseModel
from typing import Optional
from ..settings import settings
from ..utils import load_repo_config, get_db_path

router = APIRouter(prefix="/references", tags=["references"])


def _db():
    from rag.reference_db import ReferenceDB
    try:
        repo_cfg = load_repo_config(settings.repo_root)
        db_path = get_db_path(repo_cfg, settings.repo_root)
    except FileNotFoundError:
        db_path = str(settings.repo_root / "data" / "novels.db")
    return ReferenceDB(db_path)


# ═══ Works ═══════════════════════════════════════════════

class WorkCreate(BaseModel):
    title: str
    media_type: str = "web_novel"
    source: str = "manual"
    creator: str = ""
    genre: str = ""
    tags: list[str] = []
    user_rating: Optional[int] = None
    user_summary: Optional[str] = None
    user_why_i_like: Optional[str] = None
    learning_dimensions: list[str] = []
    has_full_text: bool = False


class WorkUpdate(BaseModel):
    title: Optional[str] = None
    creator: Optional[str] = None
    genre: Optional[str] = None
    media_type: Optional[str] = None
    user_rating: Optional[int] = None
    user_summary: Optional[str] = None
    user_why_i_like: Optional[str] = None
    learning_dimensions: Optional[list[str]] = None
    tags: Optional[list[str]] = None


@router.get("/works")
def list_works(
    media_type: Optional[str] = None,
    source: Optional[str] = None,
    genre: Optional[str] = None,
    search: Optional[str] = None,
    preprocessing_status: Optional[str] = None,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    db = _db()
    return {
        "items": db.list_works(
            media_type=media_type, source=source, genre=genre,
            search=search, preprocessing_status=preprocessing_status,
            limit=limit, offset=offset,
        ),
        "total": db.count_works(
            media_type=media_type,
            preprocessing_status=preprocessing_status,
        ),
    }


@router.get("/works/{ref_id}")
def get_work(ref_id: str):
    w = _db().get_work(ref_id)
    if not w:
        raise HTTPException(404, "not found")
    return w


@router.post("/works")
def create_work(body: WorkCreate):
    return _db().create_work(
        title=body.title, media_type=body.media_type, source=body.source,
        creator=body.creator, genre=body.genre, tags=body.tags,
        user_rating=body.user_rating, user_summary=body.user_summary,
        user_why_i_like=body.user_why_i_like,
        learning_dimensions=body.learning_dimensions,
        has_full_text=body.has_full_text,
    )


@router.post("/works/upload")
async def upload_work(
    file: UploadFile = File(...),
    title: str = Form(...),
    creator: str = Form(""),
    genre: str = Form(""),
    media_type: str = Form("web_novel"),
    user_why_i_like: str = Form(""),
):
    refs_dir = settings.repo_root / "data" / "references"
    refs_dir.mkdir(parents=True, exist_ok=True)
    dest = refs_dir / (file.filename or "upload.txt")
    dest.write_bytes(await file.read())
    return _db().create_work(
        title=title, media_type=media_type, source="file_upload",
        creator=creator, genre=genre, file_path=str(dest),
        user_why_i_like=user_why_i_like or None, has_full_text=True,
    )


@router.post("/works/{ref_id}/upload")
async def upload_text_for_work(ref_id: str, file: UploadFile = File(...)):
    w = _db().get_work(ref_id)
    if not w:
        raise HTTPException(404, "not found")
    
    refs_dir = settings.repo_root / "data" / "references"
    refs_dir.mkdir(parents=True, exist_ok=True)
    dest = refs_dir / f"{ref_id}_{file.filename or 'upload.txt'}"
    dest.write_bytes(await file.read())
    
    w = _db().update_work(
        ref_id,
        file_path=str(dest),
        has_full_text=True,
        preprocessing_status="pending"
    )
    return w


@router.put("/works/{ref_id}")
def update_work(ref_id: str, body: WorkUpdate):
    fields: dict = {}
    for k in ("title", "creator", "genre", "media_type",
              "user_rating", "user_summary", "user_why_i_like"):
        v = getattr(body, k, None)
        if v is not None:
            fields[k] = v
    if body.learning_dimensions is not None:
        fields["learning_dimensions_json"] = json.dumps(
            body.learning_dimensions, ensure_ascii=False)
    if body.tags is not None:
        fields["tags_json"] = json.dumps(body.tags, ensure_ascii=False)
    w = _db().update_work(ref_id, **fields)
    if not w:
        raise HTTPException(404, "not found")
    return w


@router.delete("/works/{ref_id}")
def delete_work(ref_id: str):
    if not _db().delete_work(ref_id):
        raise HTTPException(404, "not found")
    return {"ok": True}


# ═══ Entries ═════════════════════════════════════════════

class EntryCreate(BaseModel):
    ref_id: str
    entry_type: str = "other"
    title: str = ""
    content: str = ""
    content_source: str = "user_written"
    position_label: str = ""
    user_notes: str = ""
    learning_dimensions: list[str] = []
    user_rating: Optional[int] = None
    tags: list[str] = []


@router.get("/entries/{ref_id}")
def list_entries(ref_id: str, entry_type: Optional[str] = None):
    return {"items": _db().list_entries(ref_id, entry_type)}


@router.post("/entries")
def create_entry(body: EntryCreate):
    return _db().add_entry(
        ref_id=body.ref_id, entry_type=body.entry_type,
        content=body.content, title=body.title,
        content_source=body.content_source,
        position_label=body.position_label,
        user_notes=body.user_notes,
        learning_dimensions=body.learning_dimensions,
        user_rating=body.user_rating, tags=body.tags,
    )


@router.delete("/entries/{entry_id}")
def delete_entry(entry_id: str):
    if not _db().delete_entry(entry_id):
        raise HTTPException(404, "not found")
    return {"ok": True}


# ═══ Project links ═══════════════════════════════════════

class LinkCreate(BaseModel):
    project_id: str
    ref_id: str
    dimension: str
    entry_ids: list[str] = []
    reference_character_name: Optional[str] = None
    notes: Optional[str] = None


@router.post("/links")
def create_link(body: LinkCreate):
    return _db().link_to_project(
        body.project_id, body.ref_id, body.dimension,
        entry_ids=body.entry_ids,
        reference_character_name=body.reference_character_name,
        notes=body.notes,
    )


@router.get("/links/{project_id}")
def get_links(project_id: str):
    return {"items": _db().get_project_links(project_id)}


# ═══ Stats ═══════════════════════════════════════════════

@router.get("/stats/genres")
def genres():
    return {"genres": _db().genre_distribution()}


# ═══ Preprocessing ═══════════════════════════════════════

@router.post("/preprocess/{ref_id}")
def trigger_preprocess(ref_id: str):
    try:
        from analysis.feature_extraction.pipeline import FeatureExtractionPipeline
        try:
            repo_cfg = load_repo_config(settings.repo_root)
            db_path = get_db_path(repo_cfg, settings.repo_root)
        except FileNotFoundError:
            db_path = str(settings.repo_root / "data" / "novels.db")
        return FeatureExtractionPipeline(db_path).run(ref_id)
    except Exception as e:
        raise HTTPException(500, f"特征提取失败: {e}")


@router.post("/preprocess/batch")
def trigger_batch():
    try:
        from analysis.feature_extraction.pipeline import FeatureExtractionPipeline
        try:
            repo_cfg = load_repo_config(settings.repo_root)
            db_path = get_db_path(repo_cfg, settings.repo_root)
        except FileNotFoundError:
            db_path = str(settings.repo_root / "data" / "novels.db")
        results = FeatureExtractionPipeline(db_path).run_all_pending()
        return {"processed": len(results), "results": results}
    except Exception as e:
        raise HTTPException(500, f"批量提取失败: {e}")


@router.get("/preprocess/status")
def preprocess_status():
    db = _db()
    return {
        "pending": db.count_works(preprocessing_status="pending"),
        "done": db.count_works(preprocessing_status="done"),
        "error": db.count_works(preprocessing_status="error"),
        "total": db.count_works(),
    }