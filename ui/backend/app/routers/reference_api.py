"""
/api/references — 参考作品库 CRUD + 预处理触发 + 条目管理

This module is mid-refactor. The first 7 sub-routers (inspirations,
entries, links, stats, lora, index, patterns) have been extracted to
``ui.backend.app.routers.reference.<name>`` and re-mounted below. The
remaining endpoints (works CRUD/upload/files, preprocess, chapter
editing, segments, analysis writer, prompts, web_search) still live
in this file pending the next extraction pass.

All endpoints stay reachable at the same URLs (``/api/references/*``)
so the frontend and external callers are unaffected.
"""
from __future__ import annotations
import asyncio
import json
from pathlib import Path
from fastapi import APIRouter, HTTPException, Query, UploadFile, File, Form
from fastapi.responses import Response
from pydantic import BaseModel
from typing import Any, Optional
from ..settings import settings
from ..utils import load_repo_config, get_db_path

# The new package's _common.db() is the canonical implementation. Local
# code below still uses _db() for now to minimize churn until phase 3
# finishes extracting the remaining sections.
from .reference._common import db as _db, SERIAL_STATUS_VALUES as _SERIAL_STATUS_VALUES

router = APIRouter(prefix="/references", tags=["references"])

# Mount the already-extracted sub-routers under /api/references/*
from .reference import router as _extracted_router
router.include_router(_extracted_router)


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
    if body.serial_status is not None and body.serial_status not in _SERIAL_STATUS_VALUES:
        raise HTTPException(400, f"无效的 serial_status: {body.serial_status}")
    return _db().create_work(
        title=body.title, media_type=body.media_type, source=body.source,
        creator=body.creator, genre=body.genre, tags=body.tags,
        user_rating=body.user_rating, user_summary=body.user_summary,
        user_why_i_like=body.user_why_i_like,
        learning_dimensions=body.learning_dimensions,
        has_full_text=body.has_full_text,
        serial_status=body.serial_status,
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
async def upload_text_for_work(
    ref_id: str,
    file: UploadFile = File(...),
    append: bool = Form(False),
    separator: str = Form("\n\n"),
):
    """Upload a .txt file for the work. By default REPLACES the existing
    file. When ``append=true`` the new content is appended to the existing
    on-disk text (separated by ``separator``) — useful for serialized works
    that arrive in multiple .txt files (e.g. one per volume).

    Only .txt is accepted."""
    w = _db().get_work(ref_id)
    if not w:
        raise HTTPException(404, "not found")
    fname = file.filename or "upload.txt"
    if not fname.lower().endswith(".txt"):
        raise HTTPException(400, "仅支持 .txt 文件")

    raw = await file.read()
    try:
        new_text = raw.decode("utf-8")
    except UnicodeDecodeError:
        # Common fallback for legacy Chinese encodings.
        try:
            new_text = raw.decode("gb18030")
        except UnicodeDecodeError:
            raise HTTPException(400, "无法解析文件编码（请使用 UTF-8 或 GB18030）")

    refs_dir = settings.repo_root / "data" / "references"
    refs_dir.mkdir(parents=True, exist_ok=True)

    # Track the upload in segments_json["uploads"] so the Files tab can
    # list each upload separately and delete individual ones later.
    try:
        state = json.loads(w.get("segments_json") or "{}")
    except Exception:
        state = {}
    if not isinstance(state, dict):
        state = {}
    uploads: list[dict] = state.get("uploads") if isinstance(state.get("uploads"), list) else []
    import time as _time
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
        # Replacement wipes the uploads ledger.
        uploads = []
        char_start = 0
        char_end = len(new_text)

    # Immutable raw companion — used by preprocess_start / re-detect so
    # detection ALWAYS runs against the pristine upload, never the
    # working file (which gets mutated by 清理章节 / rename / etc.).
    # On append, we re-snapshot the combined working text as raw so the
    # new appended bytes are also discoverable on re-detect.
    raw_text = dest.read_text(encoding="utf-8")
    raw_path = Path(str(dest) + ".raw.txt")
    raw_path.write_text(raw_text, encoding="utf-8")
    uploads.append({
        "filename": fname,
        "char_start": char_start,
        "char_end": char_end,
        "uploaded_at": _time.strftime("%Y-%m-%d %H:%M:%S"),
    })
    state["uploads"] = uploads

    # Wipe any prior preprocess / segment state — the underlying text changed.
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
        # Replacing the novel text invalidates EVERY extracted result —
        # chronicle / characters / settings / style / rhythm — plus the
        # committed chapter list. Wipe them all so nothing stale leaks
        # into the new work.
        update_kwargs.update(
            plot_outline_json="",
            extracted_characters_json="",
            settings_json="",
            style_fingerprint_json="",
            rhythm_json="",
            narrative_structure_json="",
            rhythm_template_json="",
        )
    w = _db().update_work(ref_id, **update_kwargs)
    if not append:
        import sqlite3
        try:
            with sqlite3.connect(_db().db_path) as conn:
                conn.execute(
                    "DELETE FROM reference_chapters WHERE ref_id = ?", (ref_id,),
                )
                conn.commit()
        except Exception:
            pass
    return w


@router.get("/works/{ref_id}/files")
async def list_work_files(ref_id: str):
    """Lightweight file listing — METADATA ONLY (filename, size, range,
    timestamp). Does NOT read the file content. Tab switches stay
    instant even on multi-MB works.

    Per-file content is fetched lazily via ``/files/{index}/content``
    when the user opens the viewer."""
    db = _db()
    w = db.get_work(ref_id)
    if not w:
        raise HTTPException(404, "参考作品不存在")
    file_path = w.get("file_path")
    total_chars = 0
    if file_path and Path(file_path).exists():
        # Use char count from the uploads ledger when possible —
        # avoids reading the full file. Fall back to a quick stat
        # if no ledger entries exist.
        pass
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
        # Legacy fallback — happens when the work was uploaded by an
        # older build that didn't write the uploads ledger. Recover by
        # re-reading the file to get an accurate **char count** (not
        # bytes — CJK text is 3 bytes per char in UTF-8, so st_size was
        # showing 3× the real character count, which the user reported
        # as "字数翻倍" after restart). One quick read is acceptable
        # since this is the rescue path, not the normal one.
        try:
            text = Path(file_path).read_text(encoding="utf-8", errors="replace")
            char_count = len(text)
        except Exception:
            try:
                char_count = Path(file_path).stat().st_size
            except Exception:
                char_count = 0
        # Self-heal: write a synthetic uploads entry back to segments_json
        # so subsequent loads use the fast ledger path (and the "未跟踪"
        # banner disappears). On any DB error we silently fall through —
        # the read path still works.
        from os.path import basename as _bn
        synthetic_entry = {
            "filename": _bn(file_path),
            "char_start": 0,
            "char_end": char_count,
            "uploaded_at": None,
        }
        try:
            if not isinstance(state, dict):
                state = {}
            state["uploads"] = [synthetic_entry]
            db.update_work(ref_id, segments_json=json.dumps(state, ensure_ascii=False))
        except Exception:
            pass
        out.append({
            "index": 0,
            "filename": _bn(file_path),
            "char_start": 0,
            "char_end": char_count,
            "char_count": char_count,
            "uploaded_at": None,
            # Even though we now have a ledger entry, the file's history
            # is still unknown (we have no upload timestamp etc.) — show
            # "已修复" instead of the bare "未跟踪" so the user knows the
            # state is now stable.
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
    """Lazy-load the content of one uploaded file. Returns the
    full text slice for the upload's char range. Reads the file once
    in a worker thread."""
    db = _db()
    w = db.get_work(ref_id)
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
    from os.path import basename as _bn
    return {
        "index": 0,
        "filename": _bn(file_path),
        "content": full_text,
        "char_count": len(full_text),
    }


@router.delete("/works/{ref_id}/files/{index}")
async def delete_work_file(ref_id: str, index: int):
    """Remove ONE uploaded file's char range from the combined text and
    rebuild. Subsequent uploads' ranges are shifted left. Any
    preprocess state is cleared since the text changed."""
    db = _db()
    w = db.get_work(ref_id)
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
    # Update uploads ledger
    updated_uploads: list[dict] = []
    for i, u in enumerate(uploads):
        if i == index:
            continue
        ucs = int(u.get("char_start") or 0)
        uce = int(u.get("char_end") or 0)
        if uce <= cs:
            # Before removed range — unchanged
            updated_uploads.append({**u})
        elif ucs >= ce:
            # After removed range — shift left
            updated_uploads.append({
                **u,
                "char_start": ucs - removed_len,
                "char_end": uce - removed_len,
            })
        # Else: overlapping range (shouldn't happen) — drop
    state["uploads"] = updated_uploads
    await asyncio.to_thread(Path(file_path).write_text, new_text, encoding="utf-8")
    # Invalidate preprocess
    state.pop("preprocess", None)
    state.pop("custom_plan", None)
    state.pop("plan", None)
    state["results"] = {}
    state["completed"] = []
    from reference_pipeline import preprocess_jobs
    preprocess_jobs.clear(ref_id)
    db.update_work(
        ref_id,
        segments_json=json.dumps(state, ensure_ascii=False),
        preprocessing_status="pending",
        has_full_text=bool(new_text),
    )
    return {"ok": True, "remaining_files": len(updated_uploads), "new_total_chars": len(new_text)}


@router.delete("/works/{ref_id}/files")
async def delete_all_work_files(ref_id: str):
    """Wipe ALL uploaded content and the uploads ledger. The on-disk
    file is truncated to empty (kept around so the file_path remains
    valid). Preprocess state cleared."""
    db = _db()
    w = db.get_work(ref_id)
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
    db.update_work(
        ref_id,
        segments_json=json.dumps(state, ensure_ascii=False),
        preprocessing_status="pending",
        has_full_text=False,
    )
    return {"ok": True}


@router.put("/works/{ref_id}")
def update_work(ref_id: str, body: WorkUpdate):
    if body.serial_status is not None and body.serial_status not in _SERIAL_STATUS_VALUES:
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
    w = _db().update_work(ref_id, **fields)
    if not w:
        raise HTTPException(404, "not found")
    return w


@router.delete("/works/{ref_id}")
def delete_work(ref_id: str):
    if not _db().delete_work(ref_id):
        raise HTTPException(404, "not found")
    return {"ok": True}




# ═══ Preprocessing ═══════════════════════════════════════

@router.post("/preprocess/{ref_id}")
def trigger_preprocess(ref_id: str):
    try:
        db = _db()
        work = db.get_work(ref_id)
        if not work:
            raise HTTPException(404, "参考作品不存在")
        # Ensure text is available: if work has file_path but source isn't file_upload, fix it
        if work.get("file_path") and work.get("source") not in ("file_upload", "platform_crawl"):
            db.update_work(ref_id, preprocessing_status="pending")
        from reference_pipeline.pipeline import FeatureExtractionPipeline
        result = FeatureExtractionPipeline(db.db_path).run(ref_id)
        if result.get("error"):
            raise HTTPException(400, f"特征提取失败: {result['error']}")
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"特征提取失败: {e}")


@router.post("/preprocess/batch")
def trigger_batch():
    try:
        from reference_pipeline.pipeline import FeatureExtractionPipeline
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


# ═══ Analysis (editable feature extraction results) ═════

_ANALYSIS_FIELDS = frozenset({
    "style_fingerprint_json",
    "narrative_structure_json",
    "extracted_characters_json",
    "rhythm_template_json",
    "plot_outline_json",
    "settings_json",
    "rhythm_json",
})


class AnalysisUpdate(BaseModel):
    field: str
    # Accept dicts or lists (characters is a list)
    data: Any


@router.get("/works/{ref_id}/chapters")
def list_chapters(ref_id: str, preview_chars: int = Query(120, ge=0, le=2000)):
    """Return the parsed chapter structure for the work — what the preprocessor
    pulled out of the uploaded raw novel. Each chapter carries number, title,
    char_count, and an optional short preview (default 120 chars). Used by
    the 预处理 tab to surface "did chapterization actually work?"."""
    db = _db()
    w = db.get_work(ref_id)
    if not w:
        raise HTTPException(404, "参考作品不存在")
    try:
        from reference_pipeline.pipeline import FeatureExtractionPipeline
        pipe = FeatureExtractionPipeline(db.db_path)
        text = pipe._load_text(w)
        if not text:
            return {
                "chapters": [],
                "total_chapters": 0,
                "total_chars": 0,
                "has_full_text": False,
            }
        chapters = pipe._split_chapters(text)
        out: list[dict] = []
        for i, c in enumerate(chapters):
            content = c.get("content") or ""
            preview = ""
            if preview_chars > 0:
                head = content[:preview_chars].replace("\n", " ").strip()
                preview = head + ("…" if len(content) > preview_chars else "")
            from reference_pipeline.chapter_parser import visible_char_count
            out.append({
                "number": i + 1,
                "title": (c.get("title") or "").strip() or f"第 {i + 1} 章",
                "volume": (c.get("volume") or "").strip() or None,
                "char_count": visible_char_count(content),
                "preview": preview,
            })
        return {
            "chapters": out,
            "total_chapters": len(out),
            "total_chars": sum(c["char_count"] for c in out),
            "has_full_text": True,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"读取章节失败: {e}")


@router.get("/works/{ref_id}/segments/plan")
def get_segment_plan(ref_id: str):
    """Return the effective segmentation plan (user's saved custom plan if
    present, else auto-detected) along with extraction progress.
    Result includes ``is_custom: bool`` so the UI can show "Edited" state."""
    db = _db()
    w = db.get_work(ref_id)
    if not w:
        raise HTTPException(404, "参考作品不存在")
    try:
        from reference_pipeline.pipeline import FeatureExtractionPipeline
        pipe = FeatureExtractionPipeline(db.db_path)
        text = pipe._load_text(w)
        if not text:
            return {"type": "chunks", "segments": [], "completed": [], "total_chapters": 0, "is_custom": False}
        chapters = pipe._split_chapters(text)
        plan = pipe.get_effective_plan(ref_id, chapters)
        completed: list[int] = []
        try:
            state = json.loads(w.get("segments_json") or "{}")
            completed = sorted(int(k) for k in (state.get("results") or {}).keys())
        except Exception:
            pass
        return {**plan, "completed": completed}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"分段规划失败: {e}")


class SegmentPlanSaveRequest(BaseModel):
    segments: list[dict]
    plan_type: Optional[str] = None


@router.put("/works/{ref_id}/segments/plan")
def save_segment_plan(ref_id: str, body: SegmentPlanSaveRequest):
    """Save a user-edited segmentation plan. Each segment must have at
    least {start_chapter, end_chapter}; title is optional. Saving clears
    any prior per-segment extraction results because the segmentation
    has changed."""
    db = _db()
    w = db.get_work(ref_id)
    if not w:
        raise HTTPException(404, "参考作品不存在")
    if not body.segments:
        raise HTTPException(400, "请至少保留一个分段")
    try:
        from reference_pipeline.pipeline import FeatureExtractionPipeline
        pipe = FeatureExtractionPipeline(db.db_path)
        plan = pipe.save_custom_plan(
            ref_id, body.segments, plan_type=body.plan_type or "custom",
        )
        return {**plan, "completed": []}
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(500, f"保存分段计划失败: {e}")


@router.delete("/works/{ref_id}/segments/plan")
def reset_segment_plan(ref_id: str):
    """Discard the user's custom plan and revert to auto-detection."""
    db = _db()
    w = db.get_work(ref_id)
    if not w:
        raise HTTPException(404, "参考作品不存在")
    try:
        state = json.loads(w.get("segments_json") or "{}")
    except Exception:
        state = {}
    if isinstance(state, dict) and "custom_plan" in state:
        del state["custom_plan"]
        # Also clear results since they were computed against the custom plan
        state["results"] = {}
        state["completed"] = []
        db.update_work(ref_id, segments_json=json.dumps(state, ensure_ascii=False),
                       preprocessing_status="pending")
    return {"ok": True}


# ───────────────────── Preprocess job (chapter detection + author-note flagging) ─────────────────────


@router.post("/works/{ref_id}/preprocess/guess_start")
async def preprocess_guess_start(ref_id: str):
    """Kick off the async format-matching job. Returns immediately —
    the file read happens in the worker so the endpoint never blocks
    on multi-MB I/O. Frontend then polls /guess_status for progress."""
    from reference_pipeline import preprocess_jobs
    from reference_pipeline.pipeline import _load_chapter_patterns
    db = _db()
    w = db.get_work(ref_id)
    if not w:
        raise HTTPException(404, "参考作品不存在")
    file_path = w.get("file_path")
    if not file_path:
        raise HTTPException(400, "尚未上传正文")
    # Match preprocess_start: scan the immutable raw companion.
    raw_path = Path(str(file_path) + ".raw.txt")
    scan_path = str(raw_path) if raw_path.exists() else file_path
    extras = _load_chapter_patterns()
    job = await preprocess_jobs.start_guess_job_for_path(
        ref_id, scan_path, extra_patterns=extras,
    )
    return job.to_status()


@router.get("/works/{ref_id}/preprocess/guess_status")
def preprocess_guess_status(ref_id: str):
    """Return the live status of the format-matching job: progress
    (current_pattern / total_patterns), the candidate list once done,
    and the suggested winner."""
    from reference_pipeline import preprocess_jobs
    job = preprocess_jobs.get_guess_job(ref_id)
    if not job:
        return {"state": "idle", "current_pattern": 0, "total_patterns": 0,
                "candidates": [], "suggested": None}
    return job.to_status()


@router.post("/works/{ref_id}/preprocess/start")
async def preprocess_start(ref_id: str,
                            force_pattern: Optional[str] = Query(None),
                            force_patterns: Optional[str] = Query(None)):
    """Kick off (or return the existing) preprocess job for this work.
    Returns immediately — the file read + detection both happen in the
    worker task so the endpoint never blocks on multi-MB I/O.

    Pattern selection:
      - ``force_patterns``: comma-separated list of pattern names to
        use exclusively (multi-select). Takes precedence over
        ``force_pattern`` and auto-detection.
      - ``force_pattern``: single pattern (legacy). Auto-merges secondaries.
      - Neither: full auto-detect with auto-merge.
    """
    from reference_pipeline import preprocess_jobs
    from reference_pipeline.pipeline import _load_chapter_patterns
    db = _db()
    w = db.get_work(ref_id)
    if not w:
        raise HTTPException(404, "参考作品不存在")
    file_path = w.get("file_path")
    if not file_path:
        raise HTTPException(400, "尚未上传正文")
    # Detection ALWAYS reads the immutable raw companion (if present),
    # never the working file. The working file gets mutated by 清理章节 /
    # rename / etc., which would otherwise make 重新识别 see drifted
    # text. The .raw.txt file is the pristine upload snapshot.
    raw_path = Path(str(file_path) + ".raw.txt")
    detect_path = str(raw_path) if raw_path.exists() else file_path
    extras = _load_chapter_patterns()
    multi_list = [s.strip() for s in (force_patterns or "").split(",") if s.strip()] or None
    job = await preprocess_jobs.start_job_for_path(
        ref_id, detect_path, extra_patterns=extras,
        force_pattern=force_pattern, force_patterns=multi_list,
    )
    return job.to_status()


class ChapterContentEdit(BaseModel):
    content: str


@router.get("/works/{ref_id}/preprocess/chapter/{chapter_id}/content")
def get_chapter_content(ref_id: str, chapter_id: str):
    """Return the FULL content of one chapter for inline editing.
    Identified by stable per-detection ``chapter_id`` (not the display
    `number`, which can repeat across patterns).

    Fast path: cached ``content_start`` / ``content_end`` offsets from
    segments_json["preprocess"] — slice the file directly.
    Slow path: re-detect, match by chapter_id."""
    from reference_pipeline.chapter_parser import (
        detect_chapters, visible_char_count,
    )
    from reference_pipeline.pipeline import (
        FeatureExtractionPipeline, _load_chapter_patterns,
    )
    db = _db()
    w = db.get_work(ref_id)
    if not w:
        raise HTTPException(404, "参考作品不存在")

    # Fast path: cached offsets, lookup by chapter_id (with legacy
    # number-string fallback so old persisted state without chapter_id
    # still works).
    try:
        state = json.loads(w.get("segments_json") or "{}")
    except Exception:
        state = {}
    pre = (state or {}).get("preprocess") if isinstance(state, dict) else None
    cached_chapters = (pre or {}).get("chapters") if isinstance(pre, dict) else None
    file_path = w.get("file_path")

    def _match(c: dict) -> bool:
        cid = c.get("chapter_id")
        if cid and cid == chapter_id:
            return True
        # Legacy fallback — old persisted state may not carry chapter_id.
        try:
            return c.get("number") == int(chapter_id)
        except (TypeError, ValueError):
            return False

    if isinstance(cached_chapters, list) and file_path:
        # Re-anchor BOTH content_start and content_end from raw_marker
        # positions in the file, the same way the status endpoint does.
        # Guards against stale persisted offsets where either:
        #   - body overshoots into next chapter (content_end too large)
        #   - body skips its own opening (content_start too large)
        for idx, c in enumerate(cached_chapters):
            if not _match(c):
                continue
            this_marker = (c.get("raw_marker") or "").strip()
            nxt_marker = ""
            if idx + 1 < len(cached_chapters):
                nxt_marker = (cached_chapters[idx + 1].get("raw_marker") or "").strip()
            try:
                full = Path(file_path).read_text(encoding="utf-8")
            except Exception:
                full = None
            if full is None:
                continue
            tlen = len(full)
            # Locate THIS chapter's marker. Search from the previous
            # chapter's marker (to avoid TOC matches earlier in the file).
            search_from = 0
            for prev_idx in range(idx):
                prev_marker = (cached_chapters[prev_idx].get("raw_marker") or "").strip()
                if not prev_marker:
                    continue
                p = full.find("\n" + prev_marker, search_from)
                if p < 0 and search_from == 0 and full.startswith(prev_marker):
                    p = 0
                if p >= 0:
                    search_from = p + len(prev_marker) + (1 if full[p:p+1] == "\n" else 0)
            this_pos = -1
            if this_marker:
                pos_lf = full.find("\n" + this_marker, search_from)
                if pos_lf >= 0:
                    this_pos = pos_lf + 1
                elif search_from == 0 and full.startswith(this_marker):
                    this_pos = 0
            if this_pos < 0:
                # Marker not findable — fall back to cached offsets.
                cs = c.get("content_start")
                ce = c.get("content_end")
                if not (isinstance(cs, int) and isinstance(ce, int) and ce > cs and ce <= tlen):
                    continue
            else:
                # Body starts on the line AFTER the marker's line.
                line_break = full.find("\n", this_pos + len(this_marker))
                cs = (line_break + 1) if line_break >= 0 else tlen
                # Body ends at the next chapter's marker line-start, or
                # end of text if last.
                ce = tlen
                if nxt_marker:
                    nxt_lf = full.find("\n" + nxt_marker, cs)
                    if nxt_lf >= 0:
                        ce = nxt_lf + 1  # keep the \n on previous side
            body = full[cs:ce].strip()
            return {
                "chapter_id": c.get("chapter_id") or str(c.get("number")),
                "number": c.get("number"),
                "display_number": c.get("display_number") or c.get("number"),
                "title": c.get("title") or "",
                "raw_marker": c.get("raw_marker") or "",
                "content": body,
                "char_count": visible_char_count(body),
            }

    # Slow path: re-detect from scratch
    pipe = FeatureExtractionPipeline(db.db_path)
    text = pipe._load_text(w)
    if not text:
        raise HTTPException(400, "尚未上传正文")
    extras = _load_chapter_patterns()
    result = detect_chapters(text, extra_patterns=extras)
    for c in result["chapters"]:
        if _match(c):
            return {
                "chapter_id": c.get("chapter_id"),
                "number": c["number"],
                "display_number": c.get("display_number") or c["number"],
                "title": c["title"],
                "raw_marker": c.get("raw_marker") or "",
                "content": c["content"],
                "char_count": visible_char_count(c["content"]),
            }
    raise HTTPException(404, f"未找到章节 {chapter_id}")


class NewChapterBody(BaseModel):
    after_number: int | None = None  # None or 0 = insert at start
    heading: str  # e.g. "147、新章节" or "第一百四十七章 重生"
    content: str = ""


@router.post("/works/{ref_id}/preprocess/chapter/new")
async def preprocess_add_chapter(ref_id: str, body: NewChapterBody):
    """Insert a brand-new chapter after the given ordinal (None / 0 =
    at start). The user supplies the heading line in whatever format
    the rest of the work uses — detection re-runs on the rebuilt file
    so the new chapter gets a sequential ordinal."""
    from reference_pipeline.chapter_parser import insert_chapter
    return await _modify_and_redetect(
        ref_id,
        modifier=lambda txt, chs: insert_chapter(
            txt, chs, body.after_number, body.heading, body.content,
        ),
        op_label=f"add chapter after {body.after_number}",
    )


class RenameChapterBody(BaseModel):
    heading: str


def _resolve_chapter_number(chapters: list[dict], chapter_id: str) -> int:
    """Resolve a chapter_id (or legacy number string) to its unique
    `number` field within the given chapter list. Raises HTTP 404 when
    no match. The modifier helpers (rename/apply_exclusions/insert)
    still key on `number` — chapter_id is the public identifier."""
    for c in chapters:
        if c.get("chapter_id") == chapter_id:
            return c["number"]
    # Legacy: chapter_id might be a stringified number from old UIs.
    try:
        as_int = int(chapter_id)
    except (TypeError, ValueError):
        raise HTTPException(404, f"未找到章节 {chapter_id}")
    for c in chapters:
        if c.get("number") == as_int:
            return as_int
    raise HTTPException(404, f"未找到章节 {chapter_id}")


@router.patch("/works/{ref_id}/preprocess/chapter/{chapter_id}/title")
async def preprocess_rename_chapter(ref_id: str, chapter_id: str, body: RenameChapterBody):
    """Replace the heading line of a chapter — body is preserved.
    Detection re-runs to pick up the new title."""
    from reference_pipeline.chapter_parser import rename_chapter
    return await _modify_and_redetect(
        ref_id,
        modifier=lambda txt, chs: rename_chapter(
            txt, chs, _resolve_chapter_number(chs, chapter_id), body.heading,
        ),
        op_label=f"rename chapter {chapter_id}",
    )


@router.delete("/works/{ref_id}/preprocess/chapter/{chapter_id}")
async def preprocess_delete_chapter(ref_id: str, chapter_id: str):
    """Delete a single chapter from the file. Same backing logic as
    清理章节, just exposed as a per-row action."""
    from reference_pipeline.chapter_parser import apply_exclusions
    return await _modify_and_redetect(
        ref_id,
        modifier=lambda txt, chs: apply_exclusions(
            txt, chs, {_resolve_chapter_number(chs, chapter_id)},
        ),
        op_label=f"delete chapter {chapter_id}",
    )


async def _modify_and_redetect(ref_id: str, modifier, op_label: str,
                                 post_chapter_hook=None):
    """Shared helper for CRUD endpoints. All heavy I/O (file reads,
    regex passes, file writes) is offloaded to a worker thread so the
    UI's "清理中" / "保存中" spinner doesn't sit for 10+ seconds on
    multi-MB works.

    Two important details:
      1. The chapter list passed to ``modifier`` comes from the
         PERSISTED preprocess state (what the UI shows). Skipping the
         initial detect_chapters call saves seconds on every CRUD.
      2. The re-detect after modification forces all numbered built-in
         patterns + custom — but skips fallback patterns (短句标题 /
         作者说章节) so a freshly-edited short body doesn't trigger a
         phantom chapter.
    """
    from reference_pipeline.chapter_parser import (
        detect_chapters, flag_author_notes, flag_length_outliers, flag_garbled_chapters,
        make_preview, visible_char_count, find_chapter_gaps,
        _PATTERNS as _BUILTIN_PATTERNS, _UNNUMBERED_PATTERNS as _UNN,
    )
    from reference_pipeline.pipeline import (
        FeatureExtractionPipeline, _load_chapter_patterns,
    )
    from reference_pipeline import preprocess_jobs
    db = _db()
    w = db.get_work(ref_id)
    if not w:
        raise HTTPException(404, "参考作品不存在")
    pipe = FeatureExtractionPipeline(db.db_path)
    file_path = w.get("file_path")
    if not file_path:
        raise HTTPException(400, "作品没有关联的文件路径")
    text = await asyncio.to_thread(pipe._load_text, w)
    if not text:
        raise HTTPException(400, "尚未上传正文")
    extras = _load_chapter_patterns()

    # Build the modifier's chapter list. Two paths:
    #   1. Persisted offsets are VALID for current text → use them
    #      (cheap, microseconds).
    #   2. Persisted offsets are stale (a prior op rewrote the file,
    #      e.g. encoding repair) → fall back to a fresh detection.
    # The validity check anchors each persisted chapter's raw_marker
    # at the byte just before content_start; if any chapter fails,
    # the whole list is treated as stale to avoid mixing fresh + stale
    # offsets (which is exactly the failure mode that produced the
    # cross-chapter content duplication bug).
    try:
        state = json.loads(w.get("segments_json") or "{}")
    except Exception:
        state = {}
    pre = (state or {}).get("preprocess") if isinstance(state, dict) else None
    persisted = (pre or {}).get("chapters") if isinstance(pre, dict) else None
    modifier_chapters: list[dict] | None = None
    if isinstance(persisted, list) and persisted:
        candidate: list[dict] = []
        stale = False
        for c in persisted:
            cs = c.get("content_start")
            ce = c.get("content_end")
            marker = (c.get("raw_marker") or c.get("title") or "").strip()
            if not (isinstance(cs, int) and isinstance(ce, int)
                    and 0 <= cs <= ce <= len(text)):
                stale = True
                break
            # Anchor check: the raw_marker should sit immediately
            # before content_start (allowing trailing whitespace).
            if marker:
                lookback = max(0, cs - len(marker) - 8)
                window = text[lookback:cs]
                if marker not in window:
                    stale = True
                    break
            candidate.append({
                **c,
                "content": text[cs:ce].strip(),
                "raw_marker": marker,
            })
        if not stale:
            modifier_chapters = candidate
    if modifier_chapters is None:
        # No usable cache → fresh detect against current text.
        modifier_chapters = (await asyncio.to_thread(
            detect_chapters, text, extra_patterns=extras,
        ))["chapters"]

    try:
        new_text = await asyncio.to_thread(modifier, text, modifier_chapters)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if not new_text.strip():
        raise HTTPException(400, "操作后文本为空，已取消")
    src = Path(file_path)
    bak = src.with_suffix(src.suffix + ".bak")
    try:
        await asyncio.to_thread(bak.write_text, text, encoding="utf-8")
        await asyncio.to_thread(src.write_text, new_text, encoding="utf-8")
    except Exception as e:
        raise HTTPException(500, f"写入文件失败：{e}")
    numbered_builtin = [n for n, _ in _BUILTIN_PATTERNS if n not in _UNN]
    custom_names = [p.get("name") for p in (extras or []) if p.get("name")]
    force_for_redetect = numbered_builtin + [n for n in custom_names if n]

    def _redetect_and_flag():
        nd = detect_chapters(
            new_text, extra_patterns=extras,
            force_patterns=force_for_redetect,
        )
        ncs = nd["chapters"]
        flag_author_notes(ncs, extra_keywords=_load_author_keywords())
        flag_length_outliers(ncs)
        flag_garbled_chapters(ncs)
        for cc in ncs:
            pv = make_preview(cc.get("content") or "")
            cc["preview_head"] = pv["head"]
            cc["preview_tail"] = pv["tail"]
        if post_chapter_hook is not None:
            post_chapter_hook(ncs)
        return ncs
    new_chapters = await asyncio.to_thread(_redetect_and_flag)
    light = [
        {k: c.get(k) for k in (
            "chapter_id", "display_number", "number", "parsed_number", "title", "title_only", "raw_marker",
            "pattern", "volume",
            "is_author_note", "author_note_score", "author_note_reasons",
            "is_length_outlier", "outlier_kind",
            "is_split_piece", "is_edited", "had_asides_removed",
            "is_garbled", "garbled_reasons", "cannot_repair",
            "preview_head", "preview_tail",
            "content_start", "content_end",
        )} | {"char_count": visible_char_count(c.get("content") or "")}
        for c in new_chapters
    ]
    try:
        state = json.loads(w.get("segments_json") or "{}")
    except Exception:
        state = {}
    if not isinstance(state, dict):
        state = {}
    import time as _time
    state["preprocess"] = {
        "chapters": light,
        "total_chapters": len(light),
        "flagged_count": sum(1 for c in light if c.get("is_author_note")),
        "gaps": find_chapter_gaps(new_chapters),
        "parsed_at": _time.strftime("%Y-%m-%d %H:%M:%S"),
        "safety_applied": True,
    }
    state.pop("custom_plan", None)
    state.pop("plan", None)
    state["results"] = {}
    state["completed"] = []
    state["exclusion_backup"] = {
        "path": str(bak),
        "removed_chapters": [],
        "prev_char_count": len(text),
        "op": op_label,
    }
    db.update_work(
        ref_id,
        segments_json=json.dumps(state, ensure_ascii=False),
        preprocessing_status="pending",
    )
    preprocess_jobs.clear(ref_id)
    return {
        "ok": True,
        "total_chapters": len(light),
        "new_char_count": len(new_text),
        "can_undo": True,
        "op": op_label,
    }


@router.patch("/works/{ref_id}/preprocess/chapter/{chapter_id}/content")
def patch_chapter_content(ref_id: str, chapter_id: str, body: ChapterContentEdit):
    """Replace a chapter's body with ``content``. Snapshots the file to
    ``{path}.bak`` first (overwriting any earlier backup) so the user
    can undo via the existing undo_exclusions endpoint. Other chapters
    are left untouched."""
    from reference_pipeline.chapter_parser import (
        detect_chapters, replace_chapter_content, visible_char_count,
    )
    from reference_pipeline.pipeline import (
        FeatureExtractionPipeline, _load_chapter_patterns,
    )
    from reference_pipeline import preprocess_jobs
    db = _db()
    w = db.get_work(ref_id)
    if not w:
        raise HTTPException(404, "参考作品不存在")
    pipe = FeatureExtractionPipeline(db.db_path)
    text = pipe._load_text(w)
    if not text:
        raise HTTPException(400, "尚未上传正文")
    file_path = w.get("file_path")
    if not file_path:
        raise HTTPException(400, "作品没有关联的文件路径")
    extras = _load_chapter_patterns()
    result = detect_chapters(text, extra_patterns=extras)
    number = _resolve_chapter_number(result["chapters"], chapter_id)
    try:
        new_text = replace_chapter_content(
            text, result["chapters"], number, body.content,
        )
    except ValueError as e:
        raise HTTPException(404, str(e))
    src = Path(file_path)
    bak = src.with_suffix(src.suffix + ".bak")
    try:
        bak.write_text(text, encoding="utf-8")
        src.write_text(new_text, encoding="utf-8")
    except Exception as e:
        raise HTTPException(500, f"写入文件失败：{e}")
    # Update the persisted preprocess result so the chapter list
    # reflects the new content / char count without re-running
    # detection. Falls back to a re-detect ONLY when no preprocess
    # entry exists — guarantees the UI never loses the chapter list.
    try:
        state = json.loads(w.get("segments_json") or "{}")
    except Exception:
        state = {}
    if not isinstance(state, dict):
        state = {}
    from reference_pipeline.chapter_parser import make_preview
    pre = state.get("preprocess") if isinstance(state.get("preprocess"), dict) else None
    chapters_list = pre.get("chapters") if pre and isinstance(pre.get("chapters"), list) else None
    if chapters_list:
        # In-place update of the edited chapter + mark it
        for c in chapters_list:
            if c.get("number") == number:
                c["char_count"] = visible_char_count(body.content)
                pv = make_preview(body.content)
                c["preview_head"] = pv["head"]
                c["preview_tail"] = pv["tail"]
                c["is_edited"] = True
                break
        pre = {**(pre or {}), "chapters": chapters_list,
                "total_chapters": len(chapters_list)}
        state["preprocess"] = pre
    else:
        # No prior preprocess — re-detect on the new text so the UI
        # still has a chapter list to render.
        from reference_pipeline.chapter_parser import (
            detect_chapters as _dc, flag_author_notes as _fan,
            flag_length_outliers as _fol,
        )
        new_detect = _dc(new_text, extra_patterns=_load_chapter_patterns())
        new_chapters = new_detect["chapters"]
        _fan(new_chapters, extra_keywords=_load_author_keywords())
        _fol(new_chapters)
        for cc in new_chapters:
            pvv = make_preview(cc.get("content") or "")
            cc["preview_head"] = pvv["head"]
            cc["preview_tail"] = pvv["tail"]
            if cc.get("number") == number:
                cc["is_edited"] = True
        light = [
            {k: cc.get(k) for k in (
                "chapter_id", "display_number", "number", "parsed_number", "title", "title_only", "raw_marker",
                "pattern", "volume",
                "is_author_note", "author_note_score", "author_note_reasons",
                "is_length_outlier", "outlier_kind",
                "is_split_piece", "is_edited", "had_asides_removed",
            "is_garbled", "garbled_reasons",
                "preview_head", "preview_tail",
                "content_start", "content_end",
            )} | {"char_count": visible_char_count(cc.get("content") or "")}
            for cc in new_chapters
        ]
        state["preprocess"] = {
            "chapters": light,
            "total_chapters": len(light),
            "flagged_count": sum(1 for cc in light if cc.get("is_author_note")),
            "safety_applied": True,
        }
    state["exclusion_backup"] = {
        "path": str(bak),
        "removed_chapters": [],
        "prev_char_count": len(text),
        "edited_chapter": number,
    }
    # Segment plan + completed results may reference chapter offsets
    # that shifted; drop them so re-extraction starts clean.
    state.pop("custom_plan", None)
    state.pop("plan", None)
    state["results"] = {}
    state["completed"] = []
    db.update_work(
        ref_id,
        segments_json=json.dumps(state, ensure_ascii=False),
        preprocessing_status="pending",
    )
    preprocess_jobs.clear(ref_id)
    return {
        "ok": True,
        "number": number,
        "new_char_count": visible_char_count(body.content),
        "can_undo": True,
    }


# ── User-defined chapter patterns (stored in settings.json) ──


@router.post("/works/{ref_id}/preprocess/pause")
async def preprocess_pause(ref_id: str):
    from reference_pipeline import preprocess_jobs
    ok = preprocess_jobs.pause_job(ref_id)
    if not ok:
        raise HTTPException(400, "当前无运行中的预处理任务")
    return {"ok": True, "state": "paused"}


@router.post("/works/{ref_id}/preprocess/resume")
async def preprocess_resume(ref_id: str):
    from reference_pipeline import preprocess_jobs
    ok = preprocess_jobs.resume_job(ref_id)
    if not ok:
        raise HTTPException(400, "当前无暂停的预处理任务")
    return {"ok": True, "state": "running"}


@router.post("/works/{ref_id}/preprocess/cancel")
async def preprocess_cancel(ref_id: str):
    from reference_pipeline import preprocess_jobs
    ok = preprocess_jobs.cancel_job(ref_id)
    if not ok:
        raise HTTPException(400, "无任务可取消")
    return {"ok": True}


@router.get("/works/{ref_id}/preprocess/diagnostics")
def preprocess_diagnostics(ref_id: str):
    """Dump what the chapter parser actually sees: per-pattern match
    counts + sample first 8 matches, the detected winner, and the first
    400 chars of the text. Use this when the chapter list looks wrong —
    it tells you which pattern won and what it matched."""
    from reference_pipeline.chapter_parser import (
        detect_chapters, _PATTERNS as BUILTIN, _compile_extra,
    )
    from reference_pipeline.pipeline import (
        FeatureExtractionPipeline, _load_chapter_patterns,
    )
    db = _db()
    w = db.get_work(ref_id)
    if not w:
        raise HTTPException(404, "参考作品不存在")
    pipe = FeatureExtractionPipeline(db.db_path)
    text = pipe._load_text(w)
    if not text:
        raise HTTPException(400, "尚未上传正文")
    extras = _load_chapter_patterns()
    custom = _compile_extra(extras)
    all_pats = list(BUILTIN) + custom
    custom_names = {n for n, _ in custom}
    per_pattern = []
    for name, pat in all_pats:
        ms = list(pat.finditer(text))
        per_pattern.append({
            "name": name,
            "custom": name in custom_names,
            "count": len(ms),
            "samples": [
                {"pos": m.start(), "match": m.group(0)[:80]}
                for m in ms[:8]
            ],
        })
    result = detect_chapters(text, extra_patterns=extras)
    return {
        "text_len": len(text),
        "text_head": text[:400],
        "patterns": per_pattern,
        "winning_pattern": result["pattern"],
        "fallback_used": result["fallback_used"],
        "chapter_count": len(result["chapters"]),
        "chapter_summary": [
            {"number": c["number"], "title": c["title"], "len": len(c["content"])}
            for c in result["chapters"][:30]
        ],
    }


@router.get("/works/{ref_id}/preprocess/status")
def preprocess_status(ref_id: str):
    """Returns the live job status (state, current_chapter, log tail).
    Also includes the persisted detection result from segments_json
    when no in-process job is running — so the UI can render the last
    completed run after a server restart."""
    from reference_pipeline import preprocess_jobs
    db = _db()
    w = db.get_work(ref_id)
    if not w:
        raise HTTPException(404, "参考作品不存在")
    job = preprocess_jobs.get_job(ref_id)
    if job:
        # If a job just finished, persist its result so future requests
        # (after this server restarts) still see the chapter list.
        if job.state == "done" and job.chapters:
            try:
                preprocess_jobs.persist_result_to_segments(
                    ref_id, db.db_path, job.chapters,
                )
            except Exception:
                pass
        out = job.to_status()
        if job.state in ("done", "cancelled", "error"):
            from reference_pipeline.chapter_parser import (
                visible_char_count, find_chapter_gaps,
            )
            out["chapters"] = [
                {**{k: v for k, v in c.items() if k != "content"},
                 "char_count": visible_char_count(c.get("content") or "")}
                for c in job.chapters
            ]
            out["gaps"] = find_chapter_gaps(job.chapters)
        # Surface undo availability so the UI can show a "撤销清理" button
        try:
            state2 = json.loads(w.get("segments_json") or "{}")
        except Exception:
            state2 = {}
        bak_info = state2.get("exclusion_backup") if isinstance(state2, dict) else None
        out["can_undo"] = bool(bak_info and bak_info.get("path") and Path(bak_info["path"]).exists())
        out["last_removed_chapters"] = (bak_info or {}).get("removed_chapters") or []
        return out
    # No in-memory job — return persisted result if any
    try:
        state = json.loads(w.get("segments_json") or "{}")
    except Exception:
        state = {}
    pre = (state or {}).get("preprocess") if isinstance(state, dict) else None
    bak_info = state.get("exclusion_backup") if isinstance(state, dict) else None
    can_undo = bool(bak_info and bak_info.get("path") and Path(bak_info["path"]).exists())
    last_removed = (bak_info or {}).get("removed_chapters") or []
    if pre:
        # Defensive read-time fix-up: re-anchor every chapter's
        # content_start AND content_end by finding the chapter's
        # persisted raw_marker in the file. This corrects BOTH:
        #   (a) overshoot — body of chapter N includes chapter N+1's
        #       heading + opening paragraphs.
        #   (b) undershoot — body of chapter N+1 SKIPS its opening
        #       paragraphs because content_start was stale and
        #       pointed mid-body.
        # Both happen when persisted state was generated by an older
        # parser version. We re-derive cs/ce from raw_marker text
        # positions in the file. Once applied, set `safety_applied`
        # so subsequent status fetches skip the file read + re-anchor
        # work — the user reported the safety pass was slow enough to
        # be visible as tab-switch lag, and there's no point repeating
        # it once the persisted offsets are known to be correct.
        chapters = pre.get("chapters") or []
        if pre.get("safety_applied") or not chapters:
            return {
                "state": "done",
                "current_chapter": pre.get("total_chapters") or 0,
                "total_chapters": pre.get("total_chapters") or 0,
                "flagged_count": pre.get("flagged_count") or 0,
                "log": [],
                "chapters": chapters,
                "gaps": pre.get("gaps") or [],
                "persisted": True,
                "parsed_at": pre.get("parsed_at") or None,
                "can_undo": can_undo,
                "last_removed_chapters": last_removed,
            }
        try:
            from reference_pipeline.chapter_parser import make_preview as _mp
            file_path = w.get("file_path")
            raw_path = Path(str(file_path) + ".raw.txt") if file_path else None
            read_path = (raw_path if raw_path and raw_path.exists() else
                          (Path(file_path) if file_path else None))
            if read_path is not None and read_path.exists() and chapters:
                full_text = read_path.read_text(encoding="utf-8", errors="replace")
                tlen = len(full_text)
                # Pass 1: locate each chapter's marker line in document order.
                marker_starts: list[int | None] = []
                search_from = 0
                for c in chapters:
                    marker = (c.get("raw_marker") or "").strip()
                    if not marker:
                        marker_starts.append(None)
                        continue
                    # Look for the marker at a line start: either preceded
                    # by \n, or sitting at position 0.
                    pos_lf = full_text.find("\n" + marker, search_from)
                    pos_sof = -1
                    if search_from == 0 and full_text.startswith(marker):
                        pos_sof = 0
                    if pos_lf >= 0 and (pos_sof < 0 or pos_lf + 1 <= pos_sof):
                        ms = pos_lf + 1
                    elif pos_sof >= 0:
                        ms = pos_sof
                    else:
                        marker_starts.append(None)
                        continue
                    marker_starts.append(ms)
                    search_from = ms + len(marker)
                # Pass 2: derive content_start (right after the marker's
                # own line) and content_end (start of next located marker
                # OR end of text if last).
                fixed = False
                for i, c in enumerate(chapters):
                    ms = marker_starts[i]
                    if ms is None:
                        continue
                    marker = c.get("raw_marker") or ""
                    # End of the marker's line: the first \n at or after
                    # the marker's end. Body starts right after that \n.
                    line_break = full_text.find("\n", ms + len(marker))
                    new_cs = (line_break + 1) if line_break >= 0 else tlen
                    new_ce = tlen
                    for j in range(i + 1, len(chapters)):
                        if marker_starts[j] is not None:
                            new_ce = marker_starts[j]
                            break
                    if new_cs > new_ce:
                        # Defensive: a chapter whose body would be
                        # negative (markers in wrong order). Skip.
                        continue
                    if c.get("content_start") != new_cs or c.get("content_end") != new_ce:
                        c["content_start"] = new_cs
                        c["content_end"] = new_ce
                        body = full_text[new_cs:new_ce].strip()
                        pv = _mp(body)
                        c["preview_head"] = pv["head"]
                        c["preview_tail"] = pv["tail"]
                        from reference_pipeline.chapter_parser import (
                            visible_char_count as _vcc,
                        )
                        c["char_count"] = _vcc(body)
                        fixed = True
                # Mark the safety pass as applied — whether or not any
                # chapter actually needed correction — so subsequent
                # reads take the fast path above and skip the file I/O.
                state["preprocess"]["chapters"] = chapters
                state["preprocess"]["safety_applied"] = True
                try:
                    db.update_work(ref_id, segments_json=json.dumps(state, ensure_ascii=False))
                except Exception:
                    pass
        except Exception:
            pass
        return {
            "state": "done",
            "current_chapter": pre.get("total_chapters") or 0,
            "total_chapters": pre.get("total_chapters") or 0,
            "flagged_count": pre.get("flagged_count") or 0,
            "log": [],
            "chapters": chapters,
            "gaps": pre.get("gaps") or [],
            "persisted": True,
            "parsed_at": pre.get("parsed_at") or None,
            "can_undo": can_undo,
            "last_removed_chapters": last_removed,
        }
    return {
        "state": "idle",
        "current_chapter": 0,
        "total_chapters": 0,
        "flagged_count": 0,
        "log": [],
        "chapters": [],
        "can_undo": can_undo,
        "last_removed_chapters": last_removed,
    }


class ApplyExclusionsRequest(BaseModel):
    # Accept EITHER chapter_ids (preferred, collision-safe) OR
    # legacy numeric numbers. Frontend now sends chapter_ids by
    # default; older clients may still send numbers in this field.
    excluded_chapters: list[str | int] = []


@router.post("/works/{ref_id}/preprocess/apply_exclusions")
async def preprocess_apply_exclusions(ref_id: str, body: ApplyExclusionsRequest):
    """Bulk-delete the excluded chapters from the on-disk text. Each
    entry may be a chapter_id (e.g. "c12") or a legacy numeric number.
    The modifier path resolves them per-call against its chapter list.
    Routes through the same _modify_and_redetect fast path the CRUD
    endpoints use — uses the persisted chapter list (no extra detect
    pass at the start), runs the heavy I/O + regex in a thread, and
    re-detects with only numbered patterns."""
    from reference_pipeline.chapter_parser import apply_exclusions
    raw_keys = [k for k in (body.excluded_chapters or []) if k != ""]
    if not raw_keys:
        raise HTTPException(400, "未选择任何章节")

    def _modify(txt: str, chs: list[dict]) -> str:
        nums: set[int] = set()
        for key in raw_keys:
            # Try chapter_id match first
            sk = str(key)
            matched = False
            for c in chs:
                if c.get("chapter_id") == sk:
                    nums.add(c["number"])
                    matched = True
                    break
            if matched:
                continue
            # Fallback: numeric `number` match (legacy clients).
            try:
                n_int = int(sk)
            except (TypeError, ValueError):
                continue
            for c in chs:
                if c.get("number") == n_int:
                    nums.add(n_int)
                    break
        if not nums:
            raise ValueError("未匹配到任何章节")
        return apply_exclusions(txt, chs, nums)

    result = await _modify_and_redetect(
        ref_id,
        modifier=_modify,
        op_label=f"apply_exclusions {len(raw_keys)} entries",
    )
    return {
        **result,
        "removed_chapters": raw_keys,
    }


@router.post("/works/{ref_id}/preprocess/repair_garbled")
async def preprocess_repair_garbled(ref_id: str):
    """One-shot 一键修复乱码 button.

    Three-stage repair, then a survivor pass:
      1. Delete scrape-noise patterns (HTML / BBCode / random IDs /
         zero-width / watermarks). These are NOT encoding corruption —
         they're cleanly removable noise. Previously the endpoint
         skipped this step, which is why every HTML-laden chapter
         got marked 「无法修复」 even though deleting the tags would
         have fixed it.
      2. Whole-file encoding-mojibake repair — pick the transform
         (GBK↔UTF-8, Latin-1 round-trips, …) that best raises CJK
         density. Targets 锟斤拷 / U+FFFD / 方块 / Latin-1 残留 etc.
      3. Per-chapter encoding repair on chapters still flagged after
         steps 1+2. A multi-source file may need different transforms
         for different chapters.
      4. Survivors of all three passes get cannot_repair=true so the
         UI surfaces 「无法修复」 only for chapters where no automatic
         fix was possible.
    """
    from reference_pipeline.chapter_parser import (
        strip_deletable_garbled, repair_encoding, detect_encoding_mojibake,
        flag_garbled_chapters as _fg,
    )

    def _fix(txt: str, _chs):
        # Stage 1: delete scrape-noise (HTML tags, BBCode, …).
        txt = strip_deletable_garbled(txt)
        # Stage 2: whole-file encoding-mojibake fix.
        fixed, _transform = repair_encoding(txt)
        return fixed

    def _mark_unrepairable(chapters: list[dict]) -> None:
        # Stage 3 + 4: chapter-by-chapter recovery, then mark survivors.
        for c in chapters:
            if not c.get("is_garbled"):
                continue
            content = c.get("content") or ""
            if not content:
                continue
            # First try DELETING any remaining scrape-noise inside the
            # chapter — some files have noise so concentrated that the
            # whole-file pass didn't fully clean every region.
            cleaned = strip_deletable_garbled(content)
            if cleaned != content:
                c["content"] = cleaned
                _fg([c])
                if not c.get("is_garbled"):
                    continue
                content = cleaned
            # Then try a per-chapter encoding round-trip.
            guess = detect_encoding_mojibake(content)
            if guess and guess.get("fixed_text"):
                c["content"] = guess["fixed_text"]
                _fg([c])
                if not c.get("is_garbled"):
                    continue
            # Nothing worked — surface 「无法修复」.
            c["cannot_repair"] = True

    return await _modify_and_redetect(
        ref_id,
        modifier=_fix,
        op_label="repair_garbled",
        post_chapter_hook=_mark_unrepairable,
    )


@router.get("/works/{ref_id}/preprocess/aside_paragraphs")
def preprocess_aside_paragraphs(ref_id: str):
    """Return author-aside PARAGRAPHS detected inside regular chapters
    (short blocks containing 求月票 / 求订阅 / 推荐票 / 感谢 / … that the
    user wants stripped from chapter bodies WITHOUT removing the whole
    chapter). Whole-chapter author entries (作者说章节 pattern) are not
    returned here — they have their own bulk-clean modal."""
    from reference_pipeline.chapter_parser import (
        detect_chapters, detect_aside_paragraphs,
    )
    from reference_pipeline.pipeline import (
        FeatureExtractionPipeline, _load_chapter_patterns,
    )
    db = _db()
    w = db.get_work(ref_id)
    if not w:
        raise HTTPException(404, "参考作品不存在")
    pipe = FeatureExtractionPipeline(db.db_path)
    text = pipe._load_text(w)
    if not text:
        raise HTTPException(400, "尚未上传正文")
    result = detect_chapters(text, extra_patterns=_load_chapter_patterns())
    asides = detect_aside_paragraphs(result["chapters"], extra_keywords=_load_author_keywords())
    return {"asides": asides, "total_chapters": len(result["chapters"])}


class CleanAsideParagraphsBody(BaseModel):
    # Each entry: {chapter_number, para_index, text}. Text is the
    # paragraph content captured at detection time — used for
    # robust matching at apply time (indices alone can drift if
    # detection re-runs between fetch and apply).
    paragraphs: list[dict]


@router.post("/works/{ref_id}/preprocess/clean_aside_paragraphs")
def preprocess_clean_aside_paragraphs(ref_id: str, body: CleanAsideParagraphsBody):
    """Remove the specified paragraphs from their chapters and rewrite
    the file. Snapshots the original to .bak (same undo path as
    apply_exclusions). Other paragraphs in those chapters are kept."""
    from reference_pipeline.chapter_parser import (
        detect_chapters, apply_aside_paragraph_cleanup,
    )
    from reference_pipeline.pipeline import (
        FeatureExtractionPipeline, _load_chapter_patterns,
    )
    from reference_pipeline import preprocess_jobs
    db = _db()
    w = db.get_work(ref_id)
    if not w:
        raise HTTPException(404, "参考作品不存在")
    pipe = FeatureExtractionPipeline(db.db_path)
    text = pipe._load_text(w)
    if not text:
        raise HTTPException(400, "尚未上传正文")
    file_path = w.get("file_path")
    if not file_path:
        raise HTTPException(400, "作品没有关联的文件路径")
    result = detect_chapters(text, extra_patterns=_load_chapter_patterns())
    new_text = apply_aside_paragraph_cleanup(
        text, result["chapters"], body.paragraphs or [],
    )
    if not new_text.strip():
        raise HTTPException(400, "清理后文本为空，操作已取消")
    src = Path(file_path)
    bak = src.with_suffix(src.suffix + ".bak")
    try:
        bak.write_text(text, encoding="utf-8")
        src.write_text(new_text, encoding="utf-8")
    except Exception as e:
        raise HTTPException(500, f"写入文件失败：{e}")
    try:
        state = json.loads(w.get("segments_json") or "{}")
    except Exception:
        state = {}
    if not isinstance(state, dict):
        state = {}
    # Re-detect from the new text so the persisted chapter list stays
    # accurate after the cleanup. WITHOUT this the UI loses the
    # chapter list and falls back to the "匹配章节格式" empty state.
    from reference_pipeline.chapter_parser import (
        flag_author_notes, flag_length_outliers, make_preview, visible_char_count,
        find_chapter_gaps,
    )
    new_detect = detect_chapters(new_text, extra_patterns=_load_chapter_patterns())
    new_chapters = new_detect["chapters"]
    flag_author_notes(new_chapters, extra_keywords=_load_author_keywords())
    flag_length_outliers(new_chapters)
    # Compute which chapters had paragraphs removed so we can mark them
    # in the new chapter list (matches by chapter title — robust to
    # ordinal shifts after the rebuild).
    edited_titles: set[str] = set()
    prior_pre = state.get("preprocess") if isinstance(state.get("preprocess"), dict) else None
    prior_chs = prior_pre.get("chapters") if prior_pre and isinstance(prior_pre.get("chapters"), list) else []
    for p in (body.paragraphs or []):
        try:
            cn = int(p.get("chapter_number"))
        except (TypeError, ValueError):
            continue
        for prior_c in prior_chs:
            if prior_c.get("number") == cn:
                t = (prior_c.get("title") or "").strip()
                if t:
                    edited_titles.add(t)
                break
    for c in new_chapters:
        pv = make_preview(c.get("content") or "")
        c["preview_head"] = pv["head"]
        c["preview_tail"] = pv["tail"]
        if (c.get("title") or "").strip() in edited_titles:
            c["had_asides_removed"] = True
    light = [
        {k: c.get(k) for k in (
            "chapter_id", "display_number", "number", "parsed_number", "title", "title_only", "raw_marker",
            "pattern", "volume",
            "is_author_note", "author_note_score", "author_note_reasons",
            "is_length_outlier", "outlier_kind",
            "is_split_piece", "is_edited", "had_asides_removed",
            "is_garbled", "garbled_reasons",
            "preview_head", "preview_tail",
            "content_start", "content_end",
        )} | {"char_count": visible_char_count(c.get("content") or "")}
        for c in new_chapters
    ]
    state["preprocess"] = {
        "chapters": light,
        "total_chapters": len(light),
        "flagged_count": sum(1 for c in light if c.get("is_author_note")),
        "gaps": find_chapter_gaps(new_chapters),
        "safety_applied": True,
    }
    # Plan/results are still invalidated since chapter numbers may have
    # shifted; user can re-run extraction from the cleaned text.
    state.pop("custom_plan", None)
    state.pop("plan", None)
    state["results"] = {}
    state["completed"] = []
    state["exclusion_backup"] = {
        "path": str(bak),
        "removed_chapters": [],
        "removed_paragraphs": len(body.paragraphs or []),
        "prev_char_count": len(text),
    }
    db.update_work(
        ref_id,
        segments_json=json.dumps(state, ensure_ascii=False),
        preprocessing_status="pending",
    )
    preprocess_jobs.clear(ref_id)
    return {
        "ok": True,
        "removed_count": len(body.paragraphs or []),
        "new_char_count": len(new_text),
        "can_undo": True,
    }


@router.post("/works/{ref_id}/preprocess/save_all")
async def preprocess_save_all(ref_id: str):
    """Persist the current chapter list to the ``reference_chapters``
    table. Async so the full-file read (for offset-based content
    recovery) doesn't block the FastAPI event loop and freeze the
    UI on multi-MB works."""
    from reference_pipeline import preprocess_jobs
    from reference_pipeline.chapter_parser import visible_char_count
    db = _db()
    w = db.get_work(ref_id)
    if not w:
        raise HTTPException(404, "参考作品不存在")

    # Prefer the live job's full chapters (have full ``content``), else
    # fall back to the persisted ``preprocess`` view in segments_json
    # (light — no full content, only previews).
    job = preprocess_jobs.get_job(ref_id)
    chapters_src = None
    if job and job.chapters:
        chapters_src = job.chapters
    else:
        try:
            state = json.loads(w.get("segments_json") or "{}")
        except Exception:
            state = {}
        pre = (state or {}).get("preprocess") if isinstance(state, dict) else None
        if isinstance(pre, dict) and isinstance(pre.get("chapters"), list):
            chapters_src = pre["chapters"]
            # Light entries lack ``content``; we'll need to slice from
            # the file using stored offsets to populate it.
    if not chapters_src:
        raise HTTPException(400, "尚未识别到章节 — 请先点击「匹配章节格式」并完成识别")

    # Load full text once for offset-based content recovery (light path).
    # Offloaded to a thread so a multi-MB read doesn't block the event
    # loop (was freezing the UI for several seconds on big works).
    full_text = None
    if any("content" not in c or not c.get("content") for c in chapters_src):
        file_path = w.get("file_path")
        if file_path:
            try:
                full_text = await asyncio.to_thread(
                    Path(file_path).read_text, encoding="utf-8",
                )
            except Exception:
                full_text = None

    import sqlite3
    rows = []
    for c in chapters_src:
        body = c.get("content")
        if not body and full_text is not None:
            cs = c.get("content_start")
            ce = c.get("content_end")
            if isinstance(cs, int) and isinstance(ce, int) and 0 <= cs < ce <= len(full_text):
                body = full_text[cs:ce].strip()
        body = body or ""
        rows.append((
            ref_id, int(c.get("number") or 0),
            c.get("title") or "",
            c.get("raw_marker") or "",
            c.get("pattern") or "",
            c.get("volume") or "",
            c.get("parsed_number"),
            visible_char_count(body),
            1 if c.get("is_author_note") else 0,
            body,
            c.get("content_start"),
            c.get("content_end"),
        ))

    # SQLite bulk insert can take a moment on thousands of rows; run in
    # a thread too.
    def _write_rows():
        with sqlite3.connect(db.db_path) as conn:
            from storage.reference_schema import ensure_reference_tables
            ensure_reference_tables(conn)
            conn.execute("DELETE FROM reference_chapters WHERE ref_id = ?", (ref_id,))
            conn.executemany(
                """
                INSERT INTO reference_chapters
                  (ref_id, number, title, raw_marker, pattern, volume, parsed_number,
                   char_count, is_author_note, content, content_start, content_end)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
            conn.commit()
    await asyncio.to_thread(_write_rows)
    return {
        "ok": True,
        "saved_count": len(rows),
        "ref_id": ref_id,
    }


@router.get("/works/{ref_id}/preprocess/saved_summary")
def preprocess_saved_summary(ref_id: str):
    """Return whether (and how many) chapters are currently persisted
    in ``reference_chapters`` for this work. The UI uses this to label
    the 保存全部章节 button (e.g. "已保存 N 章 · 重新保存")."""
    import sqlite3
    db = _db()
    with sqlite3.connect(db.db_path) as conn:
        from storage.reference_schema import ensure_reference_tables
        ensure_reference_tables(conn)
        row = conn.execute(
            "SELECT COUNT(*), MAX(saved_at) FROM reference_chapters WHERE ref_id = ?",
            (ref_id,),
        ).fetchone()
    cnt = int(row[0] or 0) if row else 0
    return {"saved_count": cnt, "saved_at": row[1] if row else None}


@router.get("/works/{ref_id}/preprocess/export_text")
def preprocess_export_text(ref_id: str):
    """Concatenate every committed (cleaned) chapter from
    ``reference_chapters`` into a single .txt and return it as a file
    download. This is the post-cleanup full text — exclusions applied,
    题外话 段落 stripped — so it can be archived or re-fed elsewhere."""
    import re as _re
    import sqlite3
    from urllib.parse import quote
    db = _db()
    w = db.get_work(ref_id)
    if not w:
        raise HTTPException(404, "参考作品不存在")
    with sqlite3.connect(db.db_path) as conn:
        from storage.reference_schema import ensure_reference_tables
        ensure_reference_tables(conn)
        rows = conn.execute(
            "SELECT number, raw_marker, title, content FROM reference_chapters "
            "WHERE ref_id = ? ORDER BY number",
            (ref_id,),
        ).fetchall()
    if not rows:
        raise HTTPException(400, "数据库中没有已存储的章节 — 请先在「预处理」保存章节")
    parts: list[str] = []
    for num, raw_marker, title, content in rows:
        heading = (raw_marker or "").strip() or (title or "").strip() or f"第{num}章"
        body = (content or "").strip()
        parts.append(f"{heading}\n{body}" if body else heading)
    text = "\n\n".join(parts)
    title = (w.get("title") or ref_id).strip()
    safe = _re.sub(r"[^\w一-鿿-]", "_", title)[:60] or ref_id
    fname = f"{safe}_已清理全文.txt"
    return Response(
        content=text,
        media_type="text/plain; charset=utf-8",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(fname)}",
        },
    )


@router.get("/works/{ref_id}/preprocess/saved_chapters")
def preprocess_saved_chapters(ref_id: str):
    """Return the chapter list persisted in ``reference_chapters`` (the
    committed DB store). The 预处理 tab's read-only「已存储章节」view
    reads from HERE — not from the transient detection state — so it
    survives a reload even when the in-progress detection state is
    empty."""
    import sqlite3
    db = _db()
    with sqlite3.connect(db.db_path) as conn:
        from storage.reference_schema import ensure_reference_tables
        ensure_reference_tables(conn)
        rows = conn.execute(
            "SELECT number, title, volume, char_count, is_author_note "
            "FROM reference_chapters WHERE ref_id = ? ORDER BY number",
            (ref_id,),
        ).fetchall()
    return {
        "chapters": [
            {
                "number": int(r[0] or 0),
                "title": r[1] or "",
                "volume": r[2] or "",
                "char_count": int(r[3] or 0),
                "is_author_note": bool(r[4]),
            }
            for r in rows
        ],
    }


@router.post("/works/{ref_id}/preprocess/undo_exclusions")
def preprocess_undo_exclusions(ref_id: str):
    """Restore the pre-apply text from the most recent .bak snapshot.
    Single-level undo — the next /apply_exclusions overwrites the backup,
    so undo only reaches back one step."""
    from reference_pipeline import preprocess_jobs
    db = _db()
    w = db.get_work(ref_id)
    if not w:
        raise HTTPException(404, "参考作品不存在")
    try:
        state = json.loads(w.get("segments_json") or "{}")
    except Exception:
        state = {}
    bak_info = (state or {}).get("exclusion_backup") if isinstance(state, dict) else None
    if not bak_info or not bak_info.get("path"):
        raise HTTPException(400, "没有可撤销的清理记录")
    bak = Path(bak_info["path"])
    if not bak.exists():
        raise HTTPException(400, "备份文件已不存在，无法撤销")
    file_path = w.get("file_path")
    if not file_path:
        raise HTTPException(400, "作品没有关联的文件路径")
    try:
        text = bak.read_text(encoding="utf-8")
        Path(file_path).write_text(text, encoding="utf-8")
        bak.unlink(missing_ok=True)
    except Exception as e:
        raise HTTPException(500, f"恢复失败：{e}")
    if isinstance(state, dict):
        state.pop("preprocess", None)
        state.pop("custom_plan", None)
        state.pop("plan", None)
        state.pop("exclusion_backup", None)
        state["results"] = {}
        state["completed"] = []
    db.update_work(
        ref_id,
        segments_json=json.dumps(state, ensure_ascii=False),
        preprocessing_status="pending",
    )
    preprocess_jobs.clear(ref_id)
    return {
        "ok": True,
        "restored_char_count": len(text),
        "restored_chapters": bak_info.get("removed_chapters") or [],
    }


class SegmentTitleUpdate(BaseModel):
    title: str


@router.patch("/works/{ref_id}/segments/{index}/title")
def rename_segment_title(ref_id: str, index: int, body: SegmentTitleUpdate):
    """Rename a single segment title in-place (does NOT reset completion).
    Used for inline title edits in the timeline — "第 1–8 章" → "1954 年"."""
    db = _db()
    w = db.get_work(ref_id)
    if not w:
        raise HTTPException(404, "参考作品不存在")
    try:
        from reference_pipeline.pipeline import FeatureExtractionPipeline
        pipe = FeatureExtractionPipeline(db.db_path)
        return pipe.rename_segment_title(ref_id, index, body.title)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(500, f"重命名失败: {e}")


@router.get("/works/{ref_id}/segments/plan/auto_suggest")
def auto_suggest_plan(ref_id: str):
    """Return an auto-detected plan (volume markers OR ~100k-char chunks)
    WITHOUT persisting it. The UI uses this as the source for the
    「自动检测分卷」 button; the user can then save it as their custom
    plan via the regular PUT endpoint."""
    db = _db()
    w = db.get_work(ref_id)
    if not w:
        raise HTTPException(404, "参考作品不存在")
    try:
        from reference_pipeline.pipeline import FeatureExtractionPipeline
        pipe = FeatureExtractionPipeline(db.db_path)
        return pipe.suggest_auto_plan(ref_id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(500, f"自动检测失败: {e}")


@router.post("/works/{ref_id}/segments/plan/ai_detect")
async def ai_detect_plan(ref_id: str):
    """AI-powered volume detection.

    Tries a web-search-capable LLM first (best accuracy on published
    works), falls back to a local LLM, then to a rule-based text scan
    of chapter titles + content heads, and finally to the legacy
    parser-tag detector. The response always carries:

      - ``used_method``: which layer produced the answer
      - ``prompt_used``: the exact prompt sent to the LLM, so the user
        can paste it into a web LLM (ChatGPT / Claude.ai) when the
        configured model gave bad output

    Does NOT persist — same contract as ``/auto_suggest``."""
    db = _db()
    w = db.get_work(ref_id)
    if not w:
        raise HTTPException(404, "参考作品不存在")
    try:
        from reference_pipeline.pipeline import FeatureExtractionPipeline
        pipe = FeatureExtractionPipeline(db.db_path)
        return await pipe.ai_suggest_volume_plan(ref_id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(500, f"AI 自动检测失败: {e}")


@router.get("/works/{ref_id}/segments/plan/ai_detect_prompt")
def ai_detect_plan_prompt(ref_id: str):
    """Return the exact prompt ``ai_detect_plan`` would send, without
    actually calling any model. Lets the UI offer a "复制 prompt" path
    even when no AI is configured."""
    db = _db()
    w = db.get_work(ref_id)
    if not w:
        raise HTTPException(404, "参考作品不存在")
    try:
        from reference_pipeline.pipeline import FeatureExtractionPipeline
        pipe = FeatureExtractionPipeline(db.db_path)
        return pipe.render_volume_detect_prompt(ref_id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(500, f"生成 prompt 失败: {e}")



class SegmentRunRequest(BaseModel):
    segment_index: int
    segment_chars: Optional[int] = None
    use_ai: bool = True
    # Route the AI calls through the reference_web_search role so the
    # model can use web search to verify its extraction against the
    # real-world publication (reduces hallucinations on well-known works).
    use_web_search: bool = False
    # Per-call prompt overrides — keys are registry keys ("reference.characters",
    # "reference.settings", "reference.rhythm"). Values are full prompt text.
    # Not persisted; affects this call only.
    prompt_overrides: Optional[dict[str, str]] = None


class SegmentCommitRequest(BaseModel):
    result: dict
    # When True (the default), `commit_segment` also runs `finalize_segments`
    # so the top-level chronicle / characters / settings reflect the new
    # commit immediately. UI's "确认并入库" relies on this so users don't
    # need a second click to update the chronicle tab.
    merge_after: bool = True


@router.post("/works/{ref_id}/segments/preview")
async def preview_segment(ref_id: str, body: SegmentRunRequest):
    """Run extraction for one segment WITHOUT persisting. Returns the full
    extracted payload so the user can review before committing."""
    db = _db()
    w = db.get_work(ref_id)
    if not w:
        raise HTTPException(404, "参考作品不存在")
    try:
        from reference_pipeline.pipeline import FeatureExtractionPipeline
        pipe = FeatureExtractionPipeline(db.db_path)
        result = await pipe.compute_segment(
            ref_id, body.segment_index,
            segment_chars=body.segment_chars,
            use_ai=body.use_ai,
            use_web_search=body.use_web_search,
            prompt_overrides=body.prompt_overrides,
        )
        if "error" in result and len(result) <= 2:
            raise HTTPException(400, result.get("error") or "提取失败")
        return result
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(500, f"分段提取失败: {e}")


class ChunkExtractRequest(BaseModel):
    use_web_search: bool = False
    prompt_override: Optional[str] = None
    max_chars: int = 32_000
    # Only consulted by extract_chunk_style: when False the endpoint
    # returns just the offline NLP metrics; when True it also runs the
    # LLM for the discriminated metrics. The other per-chunk extractors
    # ignore this (they always run the model).
    use_ai: bool = False


@router.post("/works/{ref_id}/segments/{segment_index}/chunks/{chunk_index}/extract")
async def extract_chunk(ref_id: str, segment_index: int, chunk_index: int,
                         body: Optional[ChunkExtractRequest] = None):
    """Run the outline events extraction on a SINGLE chunk of a segment.

    Returns ``{events, elapsed_s, errors, chunk: {start_chapter, end_chapter,
    n_chapters, n_chars}}``. Does NOT persist — the UI calls this for each
    chunk separately, then commits accumulated events into the chronicle
    via the regular plot_outline_json PATCH.

    Mirrors the per-volume `preview_segment` flow but at chunk granularity
    so the user can confirm one chunk at a time without committing to the
    rest of the volume."""
    body = body or ChunkExtractRequest()
    db = _db()
    w = db.get_work(ref_id)
    if not w:
        raise HTTPException(404, "参考作品不存在")
    if body.max_chars < 4_000 or body.max_chars > 200_000:
        raise HTTPException(400, "max_chars 必须在 4000–200000 之间")

    try:
        from reference_pipeline.pipeline import (
            FeatureExtractionPipeline, build_work_ctx,
        )
        from reference_pipeline.ai_extractor import (
            build_segment_text_chunks, ai_extract_outline_events,
        )
        from llm.router import ModelRouter

        pipe = FeatureExtractionPipeline(db.db_path)
        text = pipe._load_text(w)
        if not text:
            raise HTTPException(400, "作品尚未上传正文")
        all_chapters = pipe._split_chapters(text)
        plan = pipe.get_effective_plan(ref_id, all_chapters)
        segs = plan["segments"]
        if not segs:
            plan = pipe.plan_segments(all_chapters)
            segs = plan["segments"]
        if segment_index < 0 or segment_index >= len(segs):
            raise HTTPException(400, "segment_index 超出范围")
        seg = segs[segment_index]
        seg_chapters = [
            {**all_chapters[j - 1], "index": j - seg["start_chapter"]}
            for j in range(seg["start_chapter"], seg["end_chapter"] + 1)
        ]
        chunks = build_segment_text_chunks(
            seg_chapters, max_chars=body.max_chars,
            segment_start_chapter=seg["start_chapter"],
        )
        if chunk_index < 0 or chunk_index >= len(chunks):
            raise HTTPException(
                400, f"chunk_index 超出范围（本段共 {len(chunks)} 个分段）",
            )
        chunk_meta = chunks[chunk_index]
        lo, hi = chunk_meta["start_chapter"], chunk_meta["end_chapter"]
        # Slice the chapters that belong to this chunk by absolute index.
        chunk_chapters = [
            c for c in seg_chapters
            if (c.get("index") or 0) + seg["start_chapter"] >= lo
            and (c.get("index") or 0) + seg["start_chapter"] <= hi
        ]

        try:
            router_inst = ModelRouter()
        except Exception as e:
            raise HTTPException(503, f"AI 路由初始化失败：{e}")

        work_ctx = build_work_ctx(w, seg, segment_index)
        # Tell the prompt which chunk this is, so the model knows the
        # extraction range exactly.
        work_ctx_for_chunk = {
            **work_ctx,
            # Override the start/end with the chunk's range so the prompt
            # shows "本次提取范围 第 X-Y 章" correctly. The other vars
            # (book title, author, etc.) stay intact.
        }

        import time as _time
        t0 = _time.perf_counter()
        try:
            events = await ai_extract_outline_events(
                chunk_chapters, router_inst,
                prompt_override=body.prompt_override,
                use_web_search=body.use_web_search,
                work_ctx=work_ctx_for_chunk,
            )
        except Exception as e:
            elapsed = round(_time.perf_counter() - t0, 2)
            return {
                "events": [],
                "elapsed_s": elapsed,
                "errors": [f"AI 提取失败：{str(e)[:240]}"],
                "chunk": {
                    "chunk_index": chunk_index,
                    "total_chunks": len(chunks),
                    "start_chapter": lo,
                    "end_chapter": hi,
                    "n_chapters": chunk_meta["n_chapters"],
                    "n_chars": chunk_meta["n_chars"],
                },
            }
        elapsed = round(_time.perf_counter() - t0, 2)
        return {
            "events": events or [],
            "elapsed_s": elapsed,
            "errors": [],
            "chunk": {
                "chunk_index": chunk_index,
                "total_chunks": len(chunks),
                "start_chapter": lo,
                "end_chapter": hi,
                "n_chapters": chunk_meta["n_chapters"],
                "n_chars": chunk_meta["n_chars"],
            },
        }
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(500, f"分段提取失败: {e}")


async def _resolve_chunk(
    db, w, ref_id: str, segment_index: int, chunk_index: int, max_chars: int,
):
    """Shared lookup for per-chunk extractors: returns
    (chunk_chapters, chunk_meta, total_chunks, work_ctx). Raises
    HTTPException on missing data."""
    from reference_pipeline.pipeline import (
        FeatureExtractionPipeline, build_work_ctx,
    )
    from reference_pipeline.ai_extractor import build_segment_text_chunks

    if max_chars < 4_000 or max_chars > 200_000:
        raise HTTPException(400, "max_chars 必须在 4000–200000 之间")
    pipe = FeatureExtractionPipeline(db.db_path)
    text = pipe._load_text(w)
    if not text:
        raise HTTPException(400, "作品尚未上传正文")
    all_chapters = pipe._split_chapters(text)
    plan = pipe.get_effective_plan(ref_id, all_chapters)
    segs = plan["segments"]
    if not segs:
        plan = pipe.plan_segments(all_chapters)
        segs = plan["segments"]
    if segment_index < 0 or segment_index >= len(segs):
        raise HTTPException(400, "segment_index 超出范围")
    seg = segs[segment_index]
    seg_chapters = [
        {**all_chapters[j - 1], "index": j - seg["start_chapter"]}
        for j in range(seg["start_chapter"], seg["end_chapter"] + 1)
    ]
    chunks = build_segment_text_chunks(
        seg_chapters, max_chars=max_chars,
        segment_start_chapter=seg["start_chapter"],
    )
    if chunk_index < 0 or chunk_index >= len(chunks):
        raise HTTPException(
            400, f"chunk_index 超出范围（本段共 {len(chunks)} 个分段）",
        )
    chunk_meta = chunks[chunk_index]
    lo, hi = chunk_meta["start_chapter"], chunk_meta["end_chapter"]
    chunk_chapters = [
        c for c in seg_chapters
        if (c.get("index") or 0) + seg["start_chapter"] >= lo
        and (c.get("index") or 0) + seg["start_chapter"] <= hi
    ]
    work_ctx = build_work_ctx(w, seg, segment_index)
    return chunk_chapters, chunk_meta, len(chunks), work_ctx


@router.post("/works/{ref_id}/segments/{segment_index}/chunks/{chunk_index}/extract_characters")
async def extract_chunk_characters(
    ref_id: str, segment_index: int, chunk_index: int,
    body: Optional[ChunkExtractRequest] = None,
):
    """Per-chunk character extraction. Returns ``{characters, elapsed_s,
    errors, chunk: {...}}``. Each character carries the rich shape
    (appearance/personality/experiences lists with chapter tags) — see
    ai_extract_characters in ai_extractor.py."""
    body = body or ChunkExtractRequest()
    db = _db()
    w = db.get_work(ref_id)
    if not w:
        raise HTTPException(404, "参考作品不存在")
    try:
        from reference_pipeline.ai_extractor import ai_extract_characters
        from llm.router import ModelRouter
        chunk_chapters, chunk_meta, total, work_ctx = await _resolve_chunk(
            db, w, ref_id, segment_index, chunk_index, body.max_chars,
        )
        try:
            router_inst = ModelRouter()
        except Exception as e:
            raise HTTPException(503, f"AI 路由初始化失败：{e}")
        import time as _time
        t0 = _time.perf_counter()
        try:
            characters = await ai_extract_characters(
                chunk_chapters, router_inst,
                prompt_override=body.prompt_override,
                use_web_search=body.use_web_search,
                work_ctx=work_ctx,
            )
            elapsed = round(_time.perf_counter() - t0, 2)
            return {
                "characters": characters or [],
                "elapsed_s": elapsed,
                "errors": [],
                "chunk": {
                    "chunk_index": chunk_index, "total_chunks": total,
                    "start_chapter": chunk_meta["start_chapter"],
                    "end_chapter": chunk_meta["end_chapter"],
                    "n_chapters": chunk_meta["n_chapters"],
                    "n_chars": chunk_meta["n_chars"],
                },
            }
        except Exception as e:
            elapsed = round(_time.perf_counter() - t0, 2)
            return {
                "characters": [], "elapsed_s": elapsed,
                "errors": [f"AI 提取失败：{str(e)[:240]}"],
                "chunk": {
                    "chunk_index": chunk_index, "total_chunks": total,
                    "start_chapter": chunk_meta["start_chapter"],
                    "end_chapter": chunk_meta["end_chapter"],
                    "n_chapters": chunk_meta["n_chapters"],
                    "n_chars": chunk_meta["n_chars"],
                },
            }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"角色提取失败: {e}")


@router.post("/works/{ref_id}/segments/{segment_index}/chunks/{chunk_index}/extract_settings")
async def extract_chunk_settings(
    ref_id: str, segment_index: int, chunk_index: int,
    body: Optional[ChunkExtractRequest] = None,
):
    """Per-chunk settings extraction. Returns ``{settings, elapsed_s,
    errors, chunk: {...}}``. Each setting carries category + concise
    title + the new ``updates`` list (one entry per chapter that
    extends or revises the setting in this chunk's range)."""
    body = body or ChunkExtractRequest()
    db = _db()
    w = db.get_work(ref_id)
    if not w:
        raise HTTPException(404, "参考作品不存在")
    try:
        from reference_pipeline.ai_extractor import ai_extract_settings
        from llm.router import ModelRouter
        chunk_chapters, chunk_meta, total, work_ctx = await _resolve_chunk(
            db, w, ref_id, segment_index, chunk_index, body.max_chars,
        )
        try:
            router_inst = ModelRouter()
        except Exception as e:
            raise HTTPException(503, f"AI 路由初始化失败：{e}")
        import time as _time
        t0 = _time.perf_counter()
        try:
            settings = await ai_extract_settings(
                chunk_chapters, router_inst,
                prompt_override=body.prompt_override,
                use_web_search=body.use_web_search,
                work_ctx=work_ctx,
            )
            elapsed = round(_time.perf_counter() - t0, 2)
            return {
                "settings": settings or [],
                "elapsed_s": elapsed,
                "errors": [],
                "chunk": {
                    "chunk_index": chunk_index, "total_chunks": total,
                    "start_chapter": chunk_meta["start_chapter"],
                    "end_chapter": chunk_meta["end_chapter"],
                    "n_chapters": chunk_meta["n_chapters"],
                    "n_chars": chunk_meta["n_chars"],
                },
            }
        except Exception as e:
            elapsed = round(_time.perf_counter() - t0, 2)
            return {
                "settings": [], "elapsed_s": elapsed,
                "errors": [f"AI 提取失败：{str(e)[:240]}"],
                "chunk": {
                    "chunk_index": chunk_index, "total_chunks": total,
                    "start_chapter": chunk_meta["start_chapter"],
                    "end_chapter": chunk_meta["end_chapter"],
                    "n_chapters": chunk_meta["n_chapters"],
                    "n_chars": chunk_meta["n_chars"],
                },
            }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"设定提取失败: {e}")


@router.post("/works/{ref_id}/segments/{segment_index}/chunks/{chunk_index}/extract_all")
async def extract_chunk_all(
    ref_id: str, segment_index: int, chunk_index: int,
    body: Optional[ChunkExtractRequest] = None,
):
    """Unified per-chunk extraction: ONE LLM call pulls events +
    characters + settings + style for the chunk, so the chapter text is
    uploaded once instead of four times. Also runs the offline NLP style
    metrics. Returns ``{events, characters, settings, style, chunk,
    elapsed_s, errors}`` where ``style`` already merges the NLP half
    (avg_sentence_length, vocab_complexity, punctuation_profile) with
    the LLM half (dialogue ratio, rhetoric, payoff/info/hook density,
    pacing, per-chapter chapter_signals).

    Does NOT persist — the UI distributes the four pieces into
    plot_outline_json / extracted_characters_json / settings_json /
    style_fingerprint_json on commit."""
    body = body or ChunkExtractRequest()
    db = _db()
    w = db.get_work(ref_id)
    if not w:
        raise HTTPException(404, "参考作品不存在")
    import time as _time
    t0 = _time.perf_counter()
    try:
        from reference_pipeline.nlp_stats import compute_nlp_style
        from reference_pipeline.ai_extractor import ai_extract_all
        from llm.router import ModelRouter
        chunk_chapters, chunk_meta, total, work_ctx = await _resolve_chunk(
            db, w, ref_id, segment_index, chunk_index, body.max_chars,
        )
        nlp = compute_nlp_style(chunk_chapters)
        errors: list[str] = []
        result: dict = {"events": [], "characters": [], "settings": [], "style": {}}
        # use_ai=False is the paste path: skip the LLM call and return
        # only the offline NLP style half (the events/characters/settings
        # and the LLM style come from the user's pasted JSON).
        if body.use_ai:
            try:
                router_inst = ModelRouter()
            except Exception as e:
                raise HTTPException(503, f"AI 路由初始化失败：{e}")
            chunk_ctx = {
                **work_ctx,
                "start_chapter": chunk_meta["start_chapter"],
                "end_chapter": chunk_meta["end_chapter"],
            }
            try:
                result = await ai_extract_all(
                    chunk_chapters, router_inst,
                    prompt_override=body.prompt_override,
                    use_web_search=body.use_web_search,
                    work_ctx=chunk_ctx,
                )
            except Exception as e:
                errors.append(f"AI 提取失败：{str(e)[:240]}")
        elapsed = round(_time.perf_counter() - t0, 2)
        return {
            "events": result.get("events") or [],
            "characters": result.get("characters") or [],
            "settings": result.get("settings") or [],
            # style = NLP half (offline) merged with the LLM half.
            "style": {**nlp, **(result.get("style") or {})},
            "n_chars": chunk_meta["n_chars"],
            "elapsed_s": elapsed,
            "errors": errors,
            "chunk": {
                "chunk_index": chunk_index, "total_chunks": total,
                "start_chapter": chunk_meta["start_chapter"],
                "end_chapter": chunk_meta["end_chapter"],
                "n_chapters": chunk_meta["n_chapters"],
                "n_chars": chunk_meta["n_chars"],
            },
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"统一特征提取失败: {e}")


@router.post("/works/{ref_id}/segments/{segment_index}/chunks/{chunk_index}/extract_style")
async def extract_chunk_style(
    ref_id: str, segment_index: int, chunk_index: int,
    body: Optional[ChunkExtractRequest] = None,
):
    """Per-chunk style fingerprint extraction. Always computes the
    deterministic NLP metrics (avg_sentence_length, vocab_complexity,
    punctuation_profile) via ``compute_nlp_style`` — offline, no tokens.
    When ``use_ai`` is set, ALSO calls the LLM (``ai_extract_style``)
    for the discriminated metrics (dialogue ratio, rhetoric, payoff /
    info / hook density, pacing).

    Returns ``{nlp, ai, n_chars, chunk, elapsed_s, errors}``. The UI
    merges nlp + ai into one per-chunk fingerprint and char-weighted-
    averages every committed chunk into the work-level fingerprint."""
    body = body or ChunkExtractRequest()
    db = _db()
    w = db.get_work(ref_id)
    if not w:
        raise HTTPException(404, "参考作品不存在")
    import time as _time
    t0 = _time.perf_counter()
    try:
        from reference_pipeline.nlp_stats import compute_nlp_style
        chunk_chapters, chunk_meta, total, work_ctx = await _resolve_chunk(
            db, w, ref_id, segment_index, chunk_index, body.max_chars,
        )
        nlp = compute_nlp_style(chunk_chapters)
        ai: dict = {}
        errors: list[str] = []
        if body.use_ai:
            from reference_pipeline.ai_extractor import ai_extract_style
            from llm.router import ModelRouter
            try:
                router_inst = ModelRouter()
            except Exception as e:
                raise HTTPException(503, f"AI 路由初始化失败：{e}")
            # Narrow the prompt's "本次范围" to this chunk's chapter range.
            chunk_ctx = {
                **work_ctx,
                "start_chapter": chunk_meta["start_chapter"],
                "end_chapter": chunk_meta["end_chapter"],
            }
            try:
                ai = await ai_extract_style(
                    chunk_chapters, router_inst,
                    prompt_override=body.prompt_override,
                    use_web_search=body.use_web_search,
                    work_ctx=chunk_ctx,
                )
            except Exception as e:
                errors.append(f"AI 提取失败：{str(e)[:240]}")
        elapsed = round(_time.perf_counter() - t0, 2)
        return {
            "nlp": nlp,
            "ai": ai,
            "n_chars": chunk_meta["n_chars"],
            "elapsed_s": elapsed,
            "errors": errors,
            "chunk": {
                "chunk_index": chunk_index, "total_chunks": total,
                "start_chapter": chunk_meta["start_chapter"],
                "end_chapter": chunk_meta["end_chapter"],
                "n_chapters": chunk_meta["n_chapters"],
                "n_chars": chunk_meta["n_chars"],
            },
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"风格指纹提取失败: {e}")


@router.get("/works/{ref_id}/segments/{segment_index}/chunks")
def list_segment_chunks(ref_id: str, segment_index: int, max_chars: int = 32_000):
    """List the chunks for a segment WITHOUT running any AI — used by
    the UI's "expand volume → show its chunks" interaction. Each chunk
    entry carries its chapter range, char count, and chunk index."""
    db = _db()
    w = db.get_work(ref_id)
    if not w:
        raise HTTPException(404, "参考作品不存在")
    if max_chars < 4_000 or max_chars > 200_000:
        raise HTTPException(400, "max_chars 必须在 4000–200000 之间")
    try:
        from reference_pipeline.pipeline import FeatureExtractionPipeline
        from reference_pipeline.ai_extractor import build_segment_text_chunks

        pipe = FeatureExtractionPipeline(db.db_path)
        text = pipe._load_text(w)
        if not text:
            raise HTTPException(400, "作品尚未上传正文")
        all_chapters = pipe._split_chapters(text)
        plan = pipe.get_effective_plan(ref_id, all_chapters)
        segs = plan["segments"]
        if not segs:
            plan = pipe.plan_segments(all_chapters)
            segs = plan["segments"]
        if segment_index < 0 or segment_index >= len(segs):
            raise HTTPException(400, "segment_index 超出范围")
        seg = segs[segment_index]
        seg_chapters = [
            {**all_chapters[j - 1], "index": j - seg["start_chapter"]}
            for j in range(seg["start_chapter"], seg["end_chapter"] + 1)
        ]
        chunks = build_segment_text_chunks(
            seg_chapters, max_chars=max_chars,
            segment_start_chapter=seg["start_chapter"],
        )
        # Strip the rendered `text` from the response — the UI only
        # needs metadata here (the prompt itself is fetched separately
        # via /preview_chunks when the user expands a chunk).
        return {
            "segment_index": segment_index,
            "volume_title": seg.get("title") or "",
            "total_chunks": len(chunks),
            "chunks": [
                {k: v for k, v in c.items() if k != "text"}
                for c in chunks
            ],
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"获取分段失败: {e}")


@router.post("/works/{ref_id}/segments/commit")
def commit_segment(ref_id: str, body: SegmentCommitRequest):
    """Persist a previewed (possibly user-edited) segment result."""
    db = _db()
    w = db.get_work(ref_id)
    if not w:
        raise HTTPException(404, "参考作品不存在")
    try:
        from reference_pipeline.pipeline import FeatureExtractionPipeline
        pipe = FeatureExtractionPipeline(db.db_path)
        return pipe.persist_segment(ref_id, body.result, merge_after=body.merge_after)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(500, f"保存失败: {e}")


@router.post("/works/{ref_id}/segments/run")
async def run_segment(ref_id: str, body: SegmentRunRequest):
    """Compute + persist in one call (legacy path)."""
    db = _db()
    w = db.get_work(ref_id)
    if not w:
        raise HTTPException(404, "参考作品不存在")
    try:
        from reference_pipeline.pipeline import FeatureExtractionPipeline
        pipe = FeatureExtractionPipeline(db.db_path)
        return await pipe.run_segment(
            ref_id, body.segment_index,
            segment_chars=body.segment_chars,
            use_ai=body.use_ai,
            use_web_search=body.use_web_search,
            prompt_overrides=body.prompt_overrides,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(500, f"分段提取失败: {e}")


@router.post("/works/{ref_id}/segments/finalize")
def finalize_segments(ref_id: str):
    """Merge all per-segment results into the top-level analysis fields."""
    db = _db()
    w = db.get_work(ref_id)
    if not w:
        raise HTTPException(404, "参考作品不存在")
    try:
        from reference_pipeline.pipeline import FeatureExtractionPipeline
        pipe = FeatureExtractionPipeline(db.db_path)
        out = pipe.finalize_segments(ref_id)
        updated = db.get_work(ref_id)
        return {"merge": out, "work": updated}
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(500, f"合并失败: {e}")


@router.post("/works/{ref_id}/segments/reset")
def reset_segments(ref_id: str):
    """Clear per-segment progress so processing can start over."""
    db = _db()
    w = db.get_work(ref_id)
    if not w:
        raise HTTPException(404, "参考作品不存在")
    db.update_work(ref_id, segments_json=None, preprocessing_status="pending")
    return {"ok": True}


# ─── Segment chat (refine a previewed extraction conversationally) ───

class ChatMessage(BaseModel):
    role: str   # "user" | "assistant"
    content: str


class SegmentChatRequest(BaseModel):
    segment_index: int
    messages: list[ChatMessage]
    current_result: Optional[dict] = None  # the previewed extraction the user is iterating on
    system_prompt_override: Optional[str] = None  # per-call override of the chat system prompt


_CHAT_SYSTEM_PROMPT = """你是参考作品分段提取的协作助手。用户正在审阅一个剧情段落的自动提取结果（编年史大纲、角色、设定），并希望与你对话调整。

当用户提出修改诉求时：
1. 用中文简短回复（≤ 3 句话）说明你做了什么调整或为什么不能调整。
2. 如果做了任何对结果的修改，必须在回复**末尾**追加一行严格的 JSON 块：
   ```json
   {"plot_outline": {...}, "characters": [...], "settings": [...]}
   ```
   JSON 中只列出**被修改的字段**（其他字段保持当前不变）。不要附加 markdown 代码块以外的字符。
3. 如果用户问问题不要求修改，回复一段文字即可，**不附 JSON 块**。

格式约束：
- plot_outline 的形态保持「epochs[].periods[].events[]」，每个 event 字段为 {subject, category, name, description, hidden?}。
- characters 项形态 {name, mentions?, intro?, speech_samples?[], appearance_chapters?, appearance_word_count?}。
- settings 项形态 {category, title, content, hidden?}。category 必须为 power_system/factions/geography/social_rules/history/worldview/other 之一。"""


def _serialize_for_chat(current: dict | None) -> str:
    if not current:
        return "（当前预览为空，请先生成预览或直接讨论）"
    keep = {
        "title": current.get("title"),
        "start_chapter": current.get("start_chapter"),
        "end_chapter": current.get("end_chapter"),
        "plot_outline": current.get("plot_outline"),
        "characters": current.get("characters"),
        "settings": current.get("settings"),
    }
    return json.dumps(keep, ensure_ascii=False, indent=2)


@router.post("/works/{ref_id}/segments/chat")
async def chat_segment(ref_id: str, body: SegmentChatRequest):
    """Conversational refinement of a previewed segment result.

    The client passes the current preview + the chat history; the model
    can revise plot_outline / characters / settings and the route returns
    both the assistant's natural-language reply and an updated result
    object the client can apply to its preview state (and later commit).
    """
    db = _db()
    w = db.get_work(ref_id)
    if not w:
        raise HTTPException(404, "参考作品不存在")
    if not body.messages:
        raise HTTPException(400, "对话内容为空")

    try:
        from llm.router import ModelRouter
        from llm.base import LLMMessage
        router_inst = ModelRouter()
    except Exception as e:
        raise HTTPException(500, f"模型路由初始化失败：{e}")

    history_lines: list[str] = []
    for m in body.messages[:-1]:
        role = "用户" if m.role == "user" else "助手"
        history_lines.append(f"【{role}】{m.content}")
    last_user = body.messages[-1].content if body.messages[-1].role == "user" else ""

    user_msg = (
        f"当前段落提取结果（JSON）：\n```\n{_serialize_for_chat(body.current_result)}\n```\n\n"
        + ("对话历史：\n" + "\n".join(history_lines) + "\n\n" if history_lines else "")
        + f"用户新的指令：{last_user}"
    )

    try:
        from reference_pipeline.prompts import render as _render_prompt
        system_prompt = _render_prompt(
            "reference.chat_system",
            override=body.system_prompt_override,
        )
        provider = router_inst._get_provider("reference_extractor")
        resp = await provider.generate(
            [
                LLMMessage(role="system", content=system_prompt),
                LLMMessage(role="user", content=user_msg),
            ],
            temperature=0.4, max_tokens=4096,
        )
        raw = resp.content or ""
    except Exception as e:
        raise HTTPException(502, f"AI 对话失败：{e}")

    import re as _re
    revised: dict = {}
    # Look for a fenced ```json … ``` block; fall back to "first { … last }"
    m = _re.search(r"```json\s*(\{[\s\S]*?\})\s*```", raw)
    if not m:
        m = _re.search(r"```\s*(\{[\s\S]*?\})\s*```", raw)
    blob = ""
    if m:
        blob = m.group(1)
        message = (raw[:m.start()] + raw[m.end():]).strip()
    else:
        # Try last top-level object
        a, b = raw.find("{"), raw.rfind("}")
        if 0 <= a < b:
            tail = raw[a:b+1]
            try:
                json.loads(tail)
                blob = tail
                message = (raw[:a] + raw[b+1:]).strip()
            except Exception:
                message = raw.strip()
        else:
            message = raw.strip()

    if blob:
        try:
            parsed = json.loads(blob)
            if isinstance(parsed, dict):
                for k in ("plot_outline", "characters", "settings"):
                    if k in parsed:
                        revised[k] = parsed[k]
        except Exception:
            pass

    return {
        "assistant_message": message or "（已应用修改）" if revised else (message or "无回复"),
        "revised": revised,
    }


@router.post("/works/{ref_id}/plot_outline/extract")
def extract_plot_outline_only(ref_id: str):
    """Re-extract plot outline from chapter splits + existing narrative analysis.
    Useful for iterating on the outline without re-running the whole pipeline.
    """
    db = _db()
    w = db.get_work(ref_id)
    if not w:
        raise HTTPException(404, "参考作品不存在")
    try:
        from reference_pipeline.pipeline import FeatureExtractionPipeline
        from reference_pipeline.narrative_extractor import (
            extract_narrative, extract_plot_outline,
        )
        pipe = FeatureExtractionPipeline(db.db_path)
        text = pipe._load_text(w)
        if not text:
            raise HTTPException(400, "缺少正文文本，无法提取大纲")
        chapters = pipe._split_chapters(text)
        narr = None
        if w.get("narrative_structure_json"):
            try:
                narr = json.loads(w["narrative_structure_json"])
            except Exception:
                narr = None
        if not narr:
            narr = extract_narrative(chapters)
        plot = extract_plot_outline(chapters, narrative=narr)
        updated = db.update_work(ref_id, plot_outline_json=json.dumps(plot, ensure_ascii=False))
        return updated
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"剧情大纲提取失败: {e}")


@router.put("/works/{ref_id}/analysis")
def update_analysis(ref_id: str, body: AnalysisUpdate):
    if body.field not in _ANALYSIS_FIELDS:
        raise HTTPException(400, f"无效字段: {body.field}。允许: {', '.join(sorted(_ANALYSIS_FIELDS))}")
    db = _db()
    w = db.get_work(ref_id)
    if not w:
        raise HTTPException(404, "参考作品不存在")
    updated = db.update_work(ref_id, **{body.field: json.dumps(body.data, ensure_ascii=False)})
    if not updated:
        raise HTTPException(500, "更新失败")
    return updated


# ═══ AI metadata completion (web search) ═══════════════════

_AI_COMPLETE_PROMPT = """请通过联网搜索查询以下 {media_type_zh}　的基本信息，并返回严格 JSON。

标题：《{title}》
{author_hint}

请填写以下字段（找不到的字段保留空字符串/null，不要编造）：
- creator: 作者全名（中文优先；电影/动漫/电视剧填导演或制作组）
- genres: 题材标签列表，3-5 个，例如 ["都市", "异术超能", "穿越"]
- serial_status: 作品状态，必须是 "ongoing"（连载中）/ "completed"（已完结）/ "hiatus"（停更）/ "unknown" 之一
- summary: 一句话梗概，≤ 50 字

只返回如下结构的 JSON 对象（不要 markdown 代码块）：
{{"creator":"","genres":[],"serial_status":"unknown","summary":""}}
"""

_MEDIA_ZH = {
    "web_novel": "网文小说", "literature": "文学作品", "poetry": "诗歌作品",
    "film": "电影", "anime": "动漫", "tv_series": "电视剧", "other": "作品",
}


# ─── Prompt template registry (read / write / preview) ───

class PromptUpdateRequest(BaseModel):
    template: Optional[str] = None  # non-null = persist; null = reset to factory


@router.get("/prompts")
def list_prompts():
    """List every registered prompt key with description + has_override flag."""
    from reference_pipeline.prompts import list_keys
    return {"items": list_keys()}


@router.get("/prompts/{key}")
def get_prompt(key: str):
    """Return the factory default + the current (possibly overridden) text."""
    from reference_pipeline.prompts import (
        DEFAULT_PROMPTS, get_default, get_template,
    )
    if key not in DEFAULT_PROMPTS:
        raise HTTPException(404, f"unknown prompt key: {key}")
    entry = DEFAULT_PROMPTS[key]
    default = get_default(key)
    current = get_template(key)
    return {
        "key": key,
        "description": entry.get("description", ""),
        "vars": list(entry.get("vars") or []),
        "default": default,
        "current": current,
        "has_override": current != default,
    }


@router.put("/prompts/{key}")
def update_prompt(key: str, body: PromptUpdateRequest):
    """Persist a new override (template != null) or reset to factory (null)."""
    from reference_pipeline.prompts import (
        DEFAULT_PROMPTS, set_template, reset,
    )
    if key not in DEFAULT_PROMPTS:
        raise HTTPException(404, f"unknown prompt key: {key}")
    if body.template is None:
        reset(key)
    else:
        set_template(key, body.template)
    return get_prompt(key)


class PromptPreviewRequest(BaseModel):
    vars: dict[str, Any] = {}
    override: Optional[str] = None


@router.post("/prompts/{key}/render")
def render_prompt(key: str, body: PromptPreviewRequest):
    """Render the prompt with explicit vars (used by the preview UI). The
    `override` field, if set, takes precedence over the persisted override
    for THIS call only — nothing is saved."""
    from reference_pipeline.prompts import DEFAULT_PROMPTS, render
    if key not in DEFAULT_PROMPTS:
        raise HTTPException(404, f"unknown prompt key: {key}")
    try:
        rendered = render(key, override=body.override, **(body.vars or {}))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"key": key, "rendered": rendered}


@router.get("/prompts/{key}/preview")
def preview_prompt(
    key: str,
    ref_id: Optional[str] = None,
    segment_index: Optional[int] = None,
    project_id: Optional[str] = None,
    chapter_id: Optional[str] = None,
    chapter_num: int = 1,
):
    """Render the prompt with the real `vars` for a specific upcoming call.

    For segment-scoped prompts (characters/settings/rhythm/chat_system),
    the server loads the work, builds the segment text and splices it in,
    so the UI shows EXACTLY what the model will see. For
    `generation.single_agent`, when `project_id` is given, the full RAG
    context is assembled so the preview matches what generation will use.
    """
    from reference_pipeline.prompts import DEFAULT_PROMPTS, render, get_template
    if key not in DEFAULT_PROMPTS:
        raise HTTPException(404, f"unknown prompt key: {key}")

    template = get_template(key)
    entry = DEFAULT_PROMPTS[key]
    required_vars = list(entry.get("vars") or [])

    # If the prompt has no vars (e.g. chat_system), return as-is
    if not required_vars:
        return {"key": key, "template": template, "rendered": template, "vars": {}}

    # Work-scoped (whole work, no segment): volume_detect just needs ref_id
    if key == "reference.volume_detect":
        if not ref_id:
            raise HTTPException(400, "ref_id required for this prompt")
        db = _db()
        w = db.get_work(ref_id)
        if not w:
            raise HTTPException(404, "参考作品不存在")
        try:
            from reference_pipeline.pipeline import FeatureExtractionPipeline
            pipe = FeatureExtractionPipeline(db.db_path)
            data = pipe.render_volume_detect_prompt(ref_id)
            return {
                "key": key, "template": template,
                "rendered": data["prompt"],
                "vars": {
                    "title": data.get("title", ""),
                    "n_chapters": data.get("total_chapters", 0),
                },
            }
        except ValueError as e:
            raise HTTPException(400, str(e))
        except Exception as e:
            raise HTTPException(500, f"构建预览失败: {e}")

    # Segment-scoped: need ref_id + segment_index → build chapter text
    if key in {"reference.characters", "reference.settings", "reference.rhythm",
               "reference.outline", "reference.style", "reference.unified"}:
        if not ref_id or segment_index is None:
            raise HTTPException(400, "ref_id + segment_index required for this prompt")
        db = _db()
        w = db.get_work(ref_id)
        if not w:
            raise HTTPException(404, "参考作品不存在")
        try:
            from reference_pipeline.pipeline import (
                FeatureExtractionPipeline, build_work_ctx,
            )
            pipe = FeatureExtractionPipeline(db.db_path)
            text = pipe._load_text(w)
            if not text:
                raise HTTPException(400, "作品尚未上传正文")
            all_chapters = pipe._split_chapters(text)
            # Honor the user's saved custom plan when previewing — the
            # user expects to see the prompt for the SAME volume layout
            # they configured, not the auto-detected fallback.
            plan = pipe.get_effective_plan(ref_id, all_chapters)
            segs = plan["segments"]
            if not segs:
                plan = pipe.plan_segments(all_chapters)
                segs = plan["segments"]
            if segment_index < 0 or segment_index >= len(segs):
                raise HTTPException(400, "segment_index 超出范围")
            seg = segs[segment_index]
            seg_chapters = [
                all_chapters[j - 1]
                for j in range(seg["start_chapter"], seg["end_chapter"] + 1)
            ]
            from reference_pipeline.ai_extractor import _build_segment_text
            seg_text, nchars = _build_segment_text(seg_chapters)
            ctx = build_work_ctx(w, seg, segment_index)
            vars_: dict[str, Any] = {
                **ctx,
                "n_chapters": len(seg_chapters),
                "n_chars": nchars,
                "text": seg_text,
                # Single-chunk default. The chunked preview endpoint
                # (preview_chunks) supplies real values for these when
                # the volume must be split for copy-to-web-LLM use.
                "chunk_index_human": 1,
                "total_chunks": 1,
                "chunk_start_chapter": ctx.get("start_chapter", "?"),
                "chunk_end_chapter": ctx.get("end_chapter", "?"),
                "chunk_n_chapters": len(seg_chapters),
            }
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(500, f"构建预览失败: {e}")
        rendered = render(key, **vars_)
        return {"key": key, "template": template, "rendered": rendered, "vars": vars_}

    # ai_complete: ref_id is enough (no segment needed)
    if key == "reference.ai_complete":
        if not ref_id:
            raise HTTPException(400, "ref_id required for this prompt")
        db = _db()
        w = db.get_work(ref_id)
        if not w:
            raise HTTPException(404, "参考作品不存在")
        author_hint = (
            f"已知作者：{w['creator']}（可用作辅助检索；如有更准确的全名请覆盖）"
            if w.get("creator") else "作者未知，请通过标题检索"
        )
        vars_ = {
            "media_type_zh": _MEDIA_ZH.get(w.get("media_type", ""), "作品"),
            "title": w.get("title", ""),
            "author_hint": author_hint,
        }
        rendered = render(key, **vars_)
        return {"key": key, "template": template, "rendered": rendered, "vars": vars_}

    # generation.single_agent: RAG-assembled, chapter-scoped. When a
    # project (and optionally a chapter) is supplied, render the prompt
    # with the SAME context that /quick-generate will use.
    if key == "generation.single_agent" and project_id:
        try:
            from ui.backend.app.routers._rag_context import (
                single_agent_vars, load_chapter_fields,
            )
            fields = load_chapter_fields(project_id, chapter_id or "")
            vars_ = single_agent_vars(
                project_id, chapter_num,
                fields.get("synopsis", ""), fields.get("time_setting", ""),
                fields.get("location", ""), fields.get("characters", []),
                fields.get("existing_content", ""),
            )
            rendered = render(key, **vars_)
            return {"key": key, "template": template, "rendered": rendered, "vars": vars_}
        except Exception as e:
            raise HTTPException(500, f"构建预览失败: {e}")

    # Fallback (assistant.* / generation.* / pipeline.* prompts): these
    # are not work-scoped, so there are no real vars to splice. Render
    # with placeholder values so the preview still shows the structure.
    placeholder_vars = {v: f"〔{v}〕" for v in required_vars}
    try:
        rendered = render(key, **placeholder_vars)
    except ValueError:
        rendered = template
    return {"key": key, "template": template, "rendered": rendered,
            "vars": placeholder_vars}


@router.get("/prompts/{key}/preview_chunks")
def preview_prompt_chunks(
    key: str,
    ref_id: str,
    segment_index: int,
    max_chars: int = 32_000,
):
    """Render the prompt for a segment as **multiple** chunks so the
    user can run an over-budget volume as N separate web-LLM calls
    instead of having content silently truncated.

    Returns::

        {"key": ..., "total_chunks": N, "chunks": [
            {"chunk_index": 0, "rendered": "...", "start_chapter": 1,
             "end_chapter": 30, "n_chapters": 30, "n_chars": 28000}, ...
        ]}

    Currently only ``reference.outline`` supports chunking — it's the
    only prompt that benefits from running each chunk independently
    (characters / settings ideally see the full text). For other keys
    this returns a single-chunk list, equivalent to the regular preview.
    """
    from reference_pipeline.prompts import (
        DEFAULT_PROMPTS, render, get_template,
    )
    if key not in DEFAULT_PROMPTS:
        raise HTTPException(404, f"unknown prompt key: {key}")
    if max_chars < 4_000 or max_chars > 200_000:
        raise HTTPException(400, "max_chars 必须在 4000–200000 之间")

    db = _db()
    w = db.get_work(ref_id)
    if not w:
        raise HTTPException(404, "参考作品不存在")
    try:
        from reference_pipeline.pipeline import (
            FeatureExtractionPipeline, build_work_ctx,
        )
        from reference_pipeline.ai_extractor import (
            build_segment_text_chunks,
        )
        pipe = FeatureExtractionPipeline(db.db_path)
        text = pipe._load_text(w)
        if not text:
            raise HTTPException(400, "作品尚未上传正文")
        all_chapters = pipe._split_chapters(text)
        plan = pipe.get_effective_plan(ref_id, all_chapters)
        segs = plan["segments"]
        if not segs:
            plan = pipe.plan_segments(all_chapters)
            segs = plan["segments"]
        if segment_index < 0 or segment_index >= len(segs):
            raise HTTPException(400, "segment_index 超出范围")
        seg = segs[segment_index]
        seg_chapters = [
            all_chapters[j - 1]
            for j in range(seg["start_chapter"], seg["end_chapter"] + 1)
        ]
        ctx = build_work_ctx(w, seg, segment_index)

        chunk_infos = build_segment_text_chunks(
            seg_chapters, max_chars=max_chars,
            segment_start_chapter=seg["start_chapter"],
        )
        if not chunk_infos:
            return {"key": key, "total_chunks": 0, "chunks": []}

        rendered_chunks: list[dict[str, Any]] = []
        for ci in chunk_infos:
            vars_: dict[str, Any] = {
                **ctx,
                "n_chapters": seg["end_chapter"] - seg["start_chapter"] + 1,
                "n_chars": ci["n_chars"],
                "text": ci["text"],
                "chunk_index_human": ci["chunk_index"] + 1,
                "total_chunks": ci["total_chunks"],
                "chunk_start_chapter": ci["start_chapter"],
                "chunk_end_chapter": ci["end_chapter"],
                "chunk_n_chapters": ci["n_chapters"],
            }
            # reference.style / reference.unified reference {start_chapter}/
            # {end_chapter}/{n_chapters} as the *chunk* range (they have no
            # separate chunk_* vars), so narrow them to this chunk.
            if key in ("reference.style", "reference.unified"):
                vars_["start_chapter"] = ci["start_chapter"]
                vars_["end_chapter"] = ci["end_chapter"]
                vars_["n_chapters"] = ci["n_chapters"]
            try:
                rendered = render(key, **vars_)
            except ValueError as e:
                # If the user's custom template doesn't reference the new
                # chunk vars, render still succeeds (only complains on
                # missing required vars). Surface the underlying error.
                raise HTTPException(400, str(e))
            rendered_chunks.append({
                "chunk_index": ci["chunk_index"],
                "rendered": rendered,
                "start_chapter": ci["start_chapter"],
                "end_chapter": ci["end_chapter"],
                "n_chapters": ci["n_chapters"],
                "n_chars": ci["n_chars"],
            })
        return {
            "key": key,
            "total_chunks": len(rendered_chunks),
            "chunks": rendered_chunks,
            "volume": {
                "index": segment_index,
                "title": ctx.get("volume_title"),
                "start_chapter": seg["start_chapter"],
                "end_chapter": seg["end_chapter"],
            },
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"构建分段预览失败: {e}")


class OutlineSummaryPromptRequest(BaseModel):
    ref_id: str
    segment_index: int
    # Flat list of event dicts (from one or more outline per-chunk runs).
    # Each dict needs at least name+description; we strip unknown keys
    # before embedding so the rendered prompt stays small.
    events: list[dict]


@router.post("/works/{ref_id}/chronicle/summarize")
async def summarize_chronicle(ref_id: str):
    """Run the chronological-summary AI pass on the currently-persisted
    chronicle. Reads `plot_outline_json`, flattens all events from
    all epochs/periods, sends them through `ai_summarize_outline`, and
    returns the reorganized chronicle (does NOT auto-persist — the UI
    decides whether to save the result).

    On AI failure the response carries `error` + the rendered prompt
    + the event list so the UI can switch the user to the manual
    web-LLM path (copy prompt → run in browser → paste back)."""
    db = _db()
    w = db.get_work(ref_id)
    if not w:
        raise HTTPException(404, "参考作品不存在")

    plot_raw = w.get("plot_outline_json") or "{}"
    try:
        plot = json.loads(plot_raw)
    except Exception:
        plot = {}
    flat_events: list[dict] = []
    for ep in (plot.get("epochs") or []):
        if not isinstance(ep, dict):
            continue
        for per in (ep.get("periods") or []):
            if not isinstance(per, dict):
                continue
            for ev in (per.get("events") or []):
                if isinstance(ev, dict):
                    flat_events.append(ev)
    if not flat_events:
        raise HTTPException(400, "编年史为空，没有事件可总结")

    try:
        from reference_pipeline.pipeline import (
            FeatureExtractionPipeline, build_work_ctx,
        )
        from reference_pipeline.prompts import render
        from reference_pipeline.ai_extractor import (
            _normalize_event, ai_summarize_outline,
        )
        pipe = FeatureExtractionPipeline(db.db_path)
        text = pipe._load_text(w)
        all_chapters = pipe._split_chapters(text) if text else []
        # Build a synthetic work-wide ctx (segment 0 covers the whole work)
        whole_segment = {
            "title": w.get("title") or "全书",
            "start_chapter": 1,
            "end_chapter": len(all_chapters) or 0,
        }
        ctx = build_work_ctx(w, whole_segment, 0)

        # Normalize+dedupe events so the prompt size stays sane.
        seen: set[tuple] = set()
        cleaned: list[dict] = []
        for ev in flat_events:
            n = _normalize_event(ev)
            if n is None:
                continue
            sig = (n["subject"], n["name"], n["description"])
            if sig in seen:
                continue
            seen.add(sig)
            cleaned.append(n)

        # Render the prompt up-front so we can return it on AI failure.
        events_json = json.dumps(cleaned, ensure_ascii=False, indent=2)
        rendered_prompt = render(
            "reference.outline_summary",
            title=ctx["title"], author=ctx["author"],
            volume_index=ctx["volume_index"], volume_title=ctx["volume_title"],
            start_chapter=ctx["start_chapter"], end_chapter=ctx["end_chapter"],
            n_chapters=len(all_chapters) or 0,
            event_count=len(cleaned), events_json=events_json,
        )

        try:
            from llm.router import ModelRouter
            router_inst = ModelRouter()
        except Exception as e:
            return {
                "ok": False,
                "error": f"AI 路由初始化失败：{e}",
                "rendered_prompt": rendered_prompt,
                "event_count": len(cleaned),
                "events": cleaned,
            }
        try:
            chronicle = await ai_summarize_outline(
                cleaned, router_inst, work_ctx=ctx,
            )
        except Exception as e:
            return {
                "ok": False,
                "error": f"AI 总结失败：{str(e)[:200]}",
                "rendered_prompt": rendered_prompt,
                "event_count": len(cleaned),
                "events": cleaned,
            }
        if not chronicle.get("epochs"):
            return {
                "ok": False,
                "error": "AI 返回为空，请改用复制 prompt 以使用AI大模型网页版的方式",
                "rendered_prompt": rendered_prompt,
                "event_count": len(cleaned),
                "events": cleaned,
            }
        return {
            "ok": True,
            "chronicle": chronicle,
            "event_count": len(cleaned),
            "rendered_prompt": rendered_prompt,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"总结失败: {e}")


# ── Plot-outline granularity ────────────────────────────────────────
# The chapter-level outline from reference_ingest + feature extraction is
# the finest grain. This lets the user condense it to a more macro view.

_OUTLINE_LEVELS = {
    "major_event": ("大事件级", "只保留推动主线与重要支线的关键事件，合并琐碎情节；事件总数约压缩到原来的三分之一。"),
    "volume": ("卷级", "每个 epoch（卷 / 大阶段）只保留 3-6 个里程碑级事件，periods 大幅合并。"),
    "book": ("全书级", "把整本书概括为 1 个 epoch、5-10 个事件，只呈现全书主干脉络。"),
}


class OutlineGranularityRequest(BaseModel):
    level: str = "major_event"   # major_event | volume | book
    prompt_only: bool = False


def _build_granularity_prompt(plot: dict, level: str) -> str:
    level_cn, level_hint = _OUTLINE_LEVELS[level]
    return (
        "[自动化数据抽取 · 不是对话] 你的输出会被 json.loads 直接解析；"
        "任何非 JSON 字符都会导致失败。\n\n"
        "下面是一部作品的「章节级」细颗粒度剧情大纲（编年史）。\n"
        f"请把它**概括**为更宏观的「{level_cn}」颗粒度大纲：{level_hint}\n\n"
        "严格禁止：寒暄 / 解释 / markdown 包装 / <think> 块 / JSON 之外的文字。\n"
        "只输出以 { 开始、} 结束的合法 JSON，结构与输入保持一致：\n"
        '{ "logline": "≤ 50 字一句话概括", "epochs": [ { "title": "大段标题", '
        '"periods": [ { "title": "时间段标题", "time_marker": "可选时间锚点", '
        '"events": [ { "subject": "主语", "category": '
        '"plot_main|plot_side|character|setting|conflict|revelation|foreshadow|other", '
        '"name": "事件名 ≤ 12 字", "description": "1-2 句客观描述", '
        '"time_marker": "可选" } ] } ] } ] }\n\n'
        "原始章节级大纲：\n"
        + json.dumps(plot, ensure_ascii=False, indent=1)
    )


@router.post("/works/{ref_id}/plot_outline/summarize")
async def summarize_plot_outline(ref_id: str, body: OutlineGranularityRequest):
    """Condense the chapter-level plot outline to a coarser granularity
    (大事件 / 卷 / 全书). Does NOT persist — the UI applies the result.
    With prompt_only=true, returns the prompt for the web-LLM path."""
    db = _db()
    w = db.get_work(ref_id)
    if not w:
        raise HTTPException(404, "参考作品不存在")
    if body.level not in _OUTLINE_LEVELS:
        raise HTTPException(400, f"无效的颗粒度：{body.level}")
    try:
        plot = json.loads(w.get("plot_outline_json") or "{}")
    except Exception:
        plot = {}
    if not (isinstance(plot, dict) and plot.get("epochs")):
        raise HTTPException(400, "暂无章节级剧情大纲，请先在「特征提取」中生成")
    prompt = _build_granularity_prompt(plot, body.level)
    if body.prompt_only:
        return {"ok": True, "prompt": prompt}
    try:
        from llm.router import ModelRouter
        from llm.base import LLMMessage
        router_inst = ModelRouter()
        provider = router_inst._get_provider("reference_extractor")
        resp = await provider.generate(
            [LLMMessage(role="user", content=prompt)],
            temperature=0.2, max_tokens=4096,
        )
        result = json.loads(_strip_json_blob(resp.content or ""))
    except Exception as e:
        return {"ok": False, "error": f"AI 概括失败：{str(e)[:200]}", "prompt": prompt}
    if not isinstance(result, dict) or not result.get("epochs"):
        return {"ok": False, "error": "AI 返回为空，请改用复制 prompt 以使用AI大模型网页版的方式", "prompt": prompt}
    return {"ok": True, "plot_outline": result}


@router.post("/prompts/reference.outline_summary/render")
def render_outline_summary_prompt(body: OutlineSummaryPromptRequest):
    """Render the chronological-summary prompt with the user's
    accumulated events spliced in. Step 2 of the manual outline flow:
    after running all per-chunk extraction prompts and accumulating
    events, the user calls this to get a ready-to-copy summary prompt
    that asks the LLM to reorder by story-time + group into periods/epochs.

    POST (not GET) because the events array can be large and shouldn't
    sit in a URL. The endpoint itself does no AI work — it just renders."""
    db = _db()
    w = db.get_work(body.ref_id)
    if not w:
        raise HTTPException(404, "参考作品不存在")
    try:
        from reference_pipeline.pipeline import (
            FeatureExtractionPipeline, build_work_ctx,
        )
        from reference_pipeline.prompts import render
        from reference_pipeline.ai_extractor import _normalize_event

        pipe = FeatureExtractionPipeline(db.db_path)
        text = pipe._load_text(w)
        if not text:
            raise HTTPException(400, "作品尚未上传正文")
        all_chapters = pipe._split_chapters(text)
        plan = pipe.get_effective_plan(body.ref_id, all_chapters)
        segs = plan["segments"]
        if not segs:
            plan = pipe.plan_segments(all_chapters)
            segs = plan["segments"]
        if body.segment_index < 0 or body.segment_index >= len(segs):
            raise HTTPException(400, "segment_index 超出范围")
        seg = segs[body.segment_index]
        ctx = build_work_ctx(w, seg, body.segment_index)

        # Normalize and dedupe events on (subject, name, description)
        # so accidental double-pastes don't bloat the rendered prompt.
        seen: set[tuple] = set()
        cleaned: list[dict] = []
        for ev in body.events or []:
            n = _normalize_event(ev)
            if n is None:
                continue
            sig = (n["subject"], n["name"], n["description"])
            if sig in seen:
                continue
            seen.add(sig)
            cleaned.append(n)

        events_json = json.dumps(cleaned, ensure_ascii=False, indent=2)
        rendered = render(
            "reference.outline_summary",
            title=ctx["title"], author=ctx["author"],
            volume_index=ctx["volume_index"], volume_title=ctx["volume_title"],
            start_chapter=ctx["start_chapter"], end_chapter=ctx["end_chapter"],
            n_chapters=seg["end_chapter"] - seg["start_chapter"] + 1,
            event_count=len(cleaned), events_json=events_json,
        )
        return {
            "key": "reference.outline_summary",
            "rendered": rendered,
            "event_count": len(cleaned),
            "dropped": len(body.events or []) - len(cleaned),
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"构建总结 prompt 失败: {e}")


@router.get("/web_search/capability")
def web_search_capability():
    """Return whether the configured ``reference_web_search`` role's
    provider+model is in the known web-search-capable set."""
    try:
        from llm.router import ModelRouter
        from llm.web_search_capabilities import supports_web_search, describe
        router_inst = ModelRouter()
        provider, model = router_inst.resolve_role("reference_web_search")
        enabled = supports_web_search(provider, model)
        return {
            "enabled": enabled,
            "provider": provider, "model": model,
            "reason": describe(provider, model),
        }
    except Exception as e:
        return {
            "enabled": False, "provider": "", "model": "",
            "reason": f"加载模型路由失败：{e}",
        }


def _strip_json_blob(raw: str) -> str:
    import re as _re
    s = (raw or "").strip()
    fence = _re.match(r"^```(?:json)?\s*(.*?)\s*```$", s, _re.DOTALL)
    if fence:
        s = fence.group(1).strip()
    a = s.find("{")
    b = s.rfind("}")
    if 0 <= a < b:
        s = s[a:b+1]
    return s


class AiCompleteRequest(BaseModel):
    prompt_override: Optional[str] = None  # per-call override


@router.post("/works/{ref_id}/ai_complete")
async def ai_complete_work(ref_id: str, body: AiCompleteRequest | None = None):
    """Use the configured ``reference_web_search`` model to fill in
    metadata fields (creator/genre/serial_status/user_summary). Only
    fills fields the user hasn't already set; user edits are preserved."""
    db = _db()
    w = db.get_work(ref_id)
    if not w:
        raise HTTPException(404, "参考作品不存在")

    try:
        from llm.router import ModelRouter
        from llm.web_search_capabilities import supports_web_search, describe
        router_inst = ModelRouter()
        provider, model = router_inst.resolve_role("reference_web_search")
    except Exception as e:
        raise HTTPException(500, f"模型路由初始化失败：{e}")

    if not supports_web_search(provider, model):
        raise HTTPException(400, describe(provider, model))

    author_hint = (
        f"已知作者：{w['creator']}（可用作辅助检索；如有更准确的全名请覆盖）"
        if w.get("creator") else "作者未知，请通过标题检索"
    )
    from reference_pipeline.prompts import render as _render_prompt
    prompt = _render_prompt(
        "reference.ai_complete",
        override=(body.prompt_override if body else None),
        media_type_zh=_MEDIA_ZH.get(w.get("media_type", ""), "作品"),
        title=w.get("title", ""),
        author_hint=author_hint,
    )

    try:
        raw = await router_inst.invoke_with_web_search(
            role="reference_web_search", prompt=prompt,
            max_tokens=1024, temperature=0.2,
        )
    except NotImplementedError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(502, f"联网调用失败：{e}")

    try:
        result = json.loads(_strip_json_blob(raw))
        if not isinstance(result, dict):
            raise ValueError("response is not a JSON object")
    except Exception as e:
        raise HTTPException(502, f"模型返回的 JSON 无法解析：{e}")

    # Only fill empty fields (preserve user edits)
    fields: dict = {}
    updated_keys: list[str] = []

    def _has(k: str) -> bool:
        v = w.get(k)
        return v not in (None, "", 0)

    new_creator = (result.get("creator") or "").strip()
    if new_creator and not _has("creator"):
        fields["creator"] = new_creator; updated_keys.append("作者")

    new_genres = result.get("genres") or []
    if isinstance(new_genres, list) and not _has("genre"):
        parts = [str(g).strip() for g in new_genres if str(g).strip()]
        if parts:
            fields["genre"] = "，".join(parts[:5])
            updated_keys.append("题材")

    new_serial = (result.get("serial_status") or "").strip().lower()
    if new_serial in _SERIAL_STATUS_VALUES and not _has("serial_status"):
        fields["serial_status"] = new_serial
        updated_keys.append("连载状态")

    new_summary = (result.get("summary") or "").strip()
    if new_summary and not _has("user_summary"):
        fields["user_summary"] = new_summary[:200]
        updated_keys.append("一句话梗概")

    if not fields:
        return {
            "work": w, "updated_keys": [],
            "message": "已有字段均不为空，未做修改（如需重新生成请先清空字段）。",
            "provider": provider, "model": model,
            "raw_response": result,
        }

    updated = db.update_work(ref_id, **fields)
    return {
        "work": updated, "updated_keys": updated_keys,
        "provider": provider, "model": model,
        "raw_response": result,
    }



