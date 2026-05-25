"""Reference works CRUD + file upload / listing / deletion.

This is the biggest unit of /api/references — works are the parent
entity that everything else (entries, links, preprocess, segments,
analysis) attaches to. The upload endpoints maintain the "uploads
ledger" inside ``segments_json`` so the Files tab can show each
upload separately and delete individual ones later.
"""
from __future__ import annotations

import asyncio
import json
import time
from os.path import basename
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel

from ...settings import settings
from ._common import SERIAL_STATUS_VALUES, db

router = APIRouter()


# ── pydantic models ───────────────────────────────────────────────


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
    serial_status: Optional[str] = None


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
    serial_status: Optional[str] = None
    # Per-chapter reader notes — list of {chapter, text}.
    chapter_comments: Optional[list[Any]] = None


# ── CRUD ──────────────────────────────────────────────────────────


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
    rdb = db()
    return {
        "items": rdb.list_works(
            media_type=media_type, source=source, genre=genre,
            search=search, preprocessing_status=preprocessing_status,
            limit=limit, offset=offset,
        ),
        "total": rdb.count_works(
            media_type=media_type,
            preprocessing_status=preprocessing_status,
        ),
    }


@router.get("/works/{ref_id}")
def get_work(ref_id: str):
    w = db().get_work(ref_id)
    if not w:
        raise HTTPException(404, "not found")
    return w


@router.post("/works")
def create_work(body: WorkCreate):
    if body.serial_status is not None and body.serial_status not in SERIAL_STATUS_VALUES:
        raise HTTPException(400, f"无效的 serial_status: {body.serial_status}")
    return db().create_work(
        title=body.title, media_type=body.media_type, source=body.source,
        creator=body.creator, genre=body.genre, tags=body.tags,
        user_rating=body.user_rating, user_summary=body.user_summary,
        user_why_i_like=body.user_why_i_like,
        learning_dimensions=body.learning_dimensions,
        has_full_text=body.has_full_text,
        serial_status=body.serial_status,
    )


@router.put("/works/{ref_id}")
def update_work(ref_id: str, body: WorkUpdate):
    if body.serial_status is not None and body.serial_status not in SERIAL_STATUS_VALUES:
        raise HTTPException(400, f"无效的 serial_status: {body.serial_status}")
    fields: dict = {}
    for k in ("title", "creator", "genre", "media_type",
              "user_rating", "user_summary", "user_why_i_like",
              "serial_status"):
        v = getattr(body, k, None)
        if v is not None:
            fields[k] = v
    if body.learning_dimensions is not None:
        fields["learning_dimensions_json"] = json.dumps(
            body.learning_dimensions, ensure_ascii=False)
    if body.tags is not None:
        fields["tags_json"] = json.dumps(body.tags, ensure_ascii=False)
    if body.chapter_comments is not None:
        fields["chapter_comments_json"] = json.dumps(
            body.chapter_comments, ensure_ascii=False)
    w = db().update_work(ref_id, **fields)
    if not w:
        raise HTTPException(404, "not found")
    return w


@router.delete("/works/{ref_id}")
def delete_work(ref_id: str):
    if not db().delete_work(ref_id):
        raise HTTPException(404, "not found")
    return {"ok": True}


# ── upload ────────────────────────────────────────────────────────


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
    return db().create_work(
        title=title, media_type=media_type, source="file_upload",
        creator=creator, genre=genre, file_path=str(dest),
        user_why_i_like=user_why_i_like or None, has_full_text=True,
    )


@router.post("/works/{ref_id}/upload")
async def upload_text_for_work(
    ref_id: str,
    file: UploadFile = File(...),
    append: bool = Form(False),
    separator: str = Form("\n\n"),
):
    """Upload a .txt file for the work. By default REPLACES the existing
    file. When ``append=true`` the new content is appended (separated by
    ``separator``) — useful for serialized works that arrive in multiple
    .txt files (e.g. one per volume). Only .txt is accepted.
    """
    rdb = db()
    w = rdb.get_work(ref_id)
    if not w:
        raise HTTPException(404, "not found")
    fname = file.filename or "upload.txt"
    if not fname.lower().endswith(".txt"):
        raise HTTPException(400, "仅支持 .txt 文件")

    raw = await file.read()
    try:
        new_text = raw.decode("utf-8")
    except UnicodeDecodeError:
        try:
            new_text = raw.decode("gb18030")
        except UnicodeDecodeError:
            raise HTTPException(400, "无法解析文件编码（请使用 UTF-8 或 GB18030）")

    refs_dir = settings.repo_root / "data" / "references"
    refs_dir.mkdir(parents=True, exist_ok=True)

    try:
        state = json.loads(w.get("segments_json") or "{}")
    except Exception:
        state = {}
    if not isinstance(state, dict):
        state = {}
    uploads: list[dict] = state.get("uploads") if isinstance(state.get("uploads"), list) else []

    if append and w.get("file_path") and Path(w["file_path"]).exists():
        dest = Path(w["file_path"])
        try:
            existing = dest.read_text(encoding="utf-8")
        except Exception:
            existing = ""
        prefix = (existing.rstrip() + (separator or "\n\n")) if existing else ""
        combined = (prefix + new_text.lstrip()).strip()
        dest.write_text(combined, encoding="utf-8")
        char_start = len(prefix)
        char_end = len(combined)
    else:
        dest = refs_dir / f"{ref_id}_{fname}"
        dest.write_text(new_text, encoding="utf-8")
        uploads = []
        char_start = 0
        char_end = len(new_text)

    # Immutable raw companion — preserves the pristine upload for re-detection.
    raw_text = dest.read_text(encoding="utf-8")
    raw_path = Path(str(dest) + ".raw.txt")
    raw_path.write_text(raw_text, encoding="utf-8")
    uploads.append({
        "filename": fname,
        "char_start": char_start,
        "char_end": char_end,
        "uploaded_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    })
    state["uploads"] = uploads

    state.pop("preprocess", None)
    state.pop("custom_plan", None)
    state.pop("plan", None)
    state["results"] = {}
    state["completed"] = []
    from reference_pipeline import preprocess_jobs
    preprocess_jobs.clear(ref_id)

    update_kwargs: dict = dict(
        file_path=str(dest),
        has_full_text=True,
        preprocessing_status="pending",
        segments_json=json.dumps(state, ensure_ascii=False),
    )
    if not append:
        # Replacing the novel text invalidates EVERY extracted result.
        update_kwargs.update(
            plot_outline_json="",
            extracted_characters_json="",
            settings_json="",
            style_fingerprint_json="",
            rhythm_json="",
            narrative_structure_json="",
            rhythm_template_json="",
        )
    w = rdb.update_work(ref_id, **update_kwargs)
    if not append:
        import sqlite3
        try:
            with sqlite3.connect(rdb.db_path) as conn:
                conn.execute(
                    "DELETE FROM reference_chapters WHERE ref_id = ?", (ref_id,),
                )
                conn.commit()
        except Exception:
            pass
    return w


# ── files (listing / individual content / individual delete / wipe all) ──


@router.get("/works/{ref_id}/files")
async def list_work_files(ref_id: str):
    """Lightweight file listing — METADATA ONLY (filename, size, range,
    timestamp). Does NOT read the file content. Per-file content is
    fetched lazily via /files/{index}/content.
    """
    rdb = db()
    w = rdb.get_work(ref_id)
    if not w:
        raise HTTPException(404, "参考作品不存在")
    file_path = w.get("file_path")
    total_chars = 0
    try:
        state = json.loads(w.get("segments_json") or "{}")
    except Exception:
        state = {}
    uploads = state.get("uploads") if isinstance(state, dict) else None
    out: list[dict] = []
    if isinstance(uploads, list) and uploads:
        for i, u in enumerate(uploads):
            cs = int(u.get("char_start") or 0)
            ce = int(u.get("char_end") or 0)
            out.append({
                "index": i,
                "filename": u.get("filename") or f"file_{i}.txt",
                "char_start": cs,
                "char_end": ce,
                "char_count": max(0, ce - cs),
                "uploaded_at": u.get("uploaded_at"),
                "legacy": bool(u.get("legacy")),
            })
            total_chars = max(total_chars, ce)
    elif file_path and Path(file_path).exists():
        # Legacy fallback — older builds didn't write the uploads ledger.
        # Recover by re-reading the file once to get accurate char count
        # (was showing 3x for CJK in UTF-8). Self-heal by writing a
        # synthetic uploads entry back to segments_json.
        try:
            text = Path(file_path).read_text(encoding="utf-8", errors="replace")
            char_count = len(text)
        except Exception:
            try:
                char_count = Path(file_path).stat().st_size
            except Exception:
                char_count = 0
        synthetic_entry = {
            "filename": basename(file_path),
            "char_start": 0,
            "char_end": char_count,
            "uploaded_at": None,
        }
        try:
            if not isinstance(state, dict):
                state = {}
            state["uploads"] = [synthetic_entry]
            rdb.update_work(ref_id, segments_json=json.dumps(state, ensure_ascii=False))
        except Exception:
            pass
        out.append({
            "index": 0,
            "filename": basename(file_path),
            "char_start": 0,
            "char_end": char_count,
            "char_count": char_count,
            "uploaded_at": None,
            "legacy": True,
        })
        total_chars = char_count
    return {
        "files": out,
        "total_chars": total_chars,
        "has_full_text": bool(w.get("has_full_text")),
        "file_path": file_path,
    }


@router.get("/works/{ref_id}/files/{index}/content")
async def get_work_file_content(ref_id: str, index: int):
    """Lazy-load the content of one uploaded file. Returns the full text
    slice for the upload's char range. Reads the file once in a worker
    thread.
    """
    rdb = db()
    w = rdb.get_work(ref_id)
    if not w:
        raise HTTPException(404, "参考作品不存在")
    file_path = w.get("file_path")
    if not file_path or not Path(file_path).exists():
        raise HTTPException(400, "尚未上传正文")
    try:
        state = json.loads(w.get("segments_json") or "{}")
    except Exception:
        state = {}
    uploads = state.get("uploads") if isinstance(state, dict) else []
    full_text = await asyncio.to_thread(Path(file_path).read_text, encoding="utf-8")
    if isinstance(uploads, list) and uploads:
        if not (0 <= index < len(uploads)):
            raise HTTPException(404, f"未找到第 {index} 个文件")
        u = uploads[index]
        cs = int(u.get("char_start") or 0)
        ce = int(u.get("char_end") or len(full_text))
        content = full_text[cs:ce] if 0 <= cs <= ce <= len(full_text) else ""
        return {
            "index": index,
            "filename": u.get("filename") or f"file_{index}.txt",
            "content": content,
            "char_count": len(content),
        }
    # Legacy single-file
    if index != 0:
        raise HTTPException(404, "未找到该文件")
    return {
        "index": 0,
        "filename": basename(file_path),
        "content": full_text,
        "char_count": len(full_text),
    }


@router.delete("/works/{ref_id}/files/{index}")
async def delete_work_file(ref_id: str, index: int):
    """Remove ONE uploaded file's char range from the combined text and
    rebuild. Subsequent uploads' ranges are shifted left. Any preprocess
    state is cleared since the text changed.
    """
    rdb = db()
    w = rdb.get_work(ref_id)
    if not w:
        raise HTTPException(404, "参考作品不存在")
    file_path = w.get("file_path")
    if not file_path or not Path(file_path).exists():
        raise HTTPException(400, "尚未上传正文")
    try:
        state = json.loads(w.get("segments_json") or "{}")
    except Exception:
        state = {}
    if not isinstance(state, dict):
        state = {}
    uploads = state.get("uploads") if isinstance(state.get("uploads"), list) else []
    if not (0 <= index < len(uploads)):
        raise HTTPException(404, f"未找到第 {index} 个文件")
    target = uploads[index]
    cs = int(target.get("char_start") or 0)
    ce = int(target.get("char_end") or 0)
    full_text = await asyncio.to_thread(Path(file_path).read_text, encoding="utf-8")
    if not (0 <= cs <= ce <= len(full_text)):
        raise HTTPException(400, "文件范围与正文不符；请重新上传")
    removed_len = ce - cs
    new_text = (full_text[:cs] + full_text[ce:]).strip()
    updated_uploads: list[dict] = []
    for i, u in enumerate(uploads):
        if i == index:
            continue
        ucs = int(u.get("char_start") or 0)
        uce = int(u.get("char_end") or 0)
        if uce <= cs:
            updated_uploads.append({**u})
        elif ucs >= ce:
            updated_uploads.append({
                **u,
                "char_start": ucs - removed_len,
                "char_end": uce - removed_len,
            })
    state["uploads"] = updated_uploads
    await asyncio.to_thread(Path(file_path).write_text, new_text, encoding="utf-8")
    state.pop("preprocess", None)
    state.pop("custom_plan", None)
    state.pop("plan", None)
    state["results"] = {}
    state["completed"] = []
    from reference_pipeline import preprocess_jobs
    preprocess_jobs.clear(ref_id)
    rdb.update_work(
        ref_id,
        segments_json=json.dumps(state, ensure_ascii=False),
        preprocessing_status="pending",
        has_full_text=bool(new_text),
    )
    return {"ok": True, "remaining_files": len(updated_uploads), "new_total_chars": len(new_text)}


@router.delete("/works/{ref_id}/files")
async def delete_all_work_files(ref_id: str):
    """Wipe ALL uploaded content and the uploads ledger. The on-disk
    file is truncated to empty (kept around so file_path remains valid).
    Preprocess state cleared.
    """
    rdb = db()
    w = rdb.get_work(ref_id)
    if not w:
        raise HTTPException(404, "参考作品不存在")
    file_path = w.get("file_path")
    if file_path and Path(file_path).exists():
        await asyncio.to_thread(Path(file_path).write_text, "", encoding="utf-8")
    try:
        state = json.loads(w.get("segments_json") or "{}")
    except Exception:
        state = {}
    if not isinstance(state, dict):
        state = {}
    state["uploads"] = []
    state.pop("preprocess", None)
    state.pop("custom_plan", None)
    state.pop("plan", None)
    state["results"] = {}
    state["completed"] = []
    from reference_pipeline import preprocess_jobs
    preprocess_jobs.clear(ref_id)
    rdb.update_work(
        ref_id,
        segments_json=json.dumps(state, ensure_ascii=False),
        preprocessing_status="pending",
        has_full_text=False,
    )
    return {"ok": True}
