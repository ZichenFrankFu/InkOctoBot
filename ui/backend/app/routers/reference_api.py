"""
/api/references — 参考作品库 CRUD + 预处理触发 + 条目管理
"""
from __future__ import annotations
import json
from pathlib import Path
from fastapi import APIRouter, HTTPException, Query, UploadFile, File, Form
from pydantic import BaseModel
from typing import Any, Optional
from ..settings import settings
from ..utils import load_repo_config, get_db_path

router = APIRouter(prefix="/references", tags=["references"])


def _db():
    from rag.reference_db import ReferenceDB
    # Test mode: use reference DB from data_dir
    if settings.test_mode and settings.data_dir:
        db_path = str(settings.data_dir / "novels.db")
        return ReferenceDB(db_path)
    try:
        repo_cfg = load_repo_config(settings.repo_root)
        db_path = get_db_path(repo_cfg, settings.repo_root)
    except FileNotFoundError:
        db_path = str(settings.repo_root / "data" / "novels.db")
    return ReferenceDB(db_path)


# ═══ Works ═══════════════════════════════════════════════

_SERIAL_STATUS_VALUES = frozenset({"ongoing", "completed", "hiatus", "unknown"})


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

    if append and w.get("file_path") and Path(w["file_path"]).exists():
        # Append to the existing file in place — keeps file_path stable so
        # downstream code that cached it sees the longer text on next read.
        dest = Path(w["file_path"])
        try:
            existing = dest.read_text(encoding="utf-8")
        except Exception:
            existing = ""
        combined = (existing.rstrip() + (separator or "\n\n") + new_text.lstrip()).strip()
        dest.write_text(combined, encoding="utf-8")
    else:
        dest = refs_dir / f"{ref_id}_{fname}"
        dest.write_text(new_text, encoding="utf-8")

    # Wipe any prior preprocess / segment state — the underlying text changed.
    try:
        state = json.loads(w.get("segments_json") or "{}")
    except Exception:
        state = {}
    if isinstance(state, dict):
        state.pop("preprocess", None)
        state.pop("custom_plan", None)
        state.pop("plan", None)
        state["results"] = {}
        state["completed"] = []
    from analysis.feature_extraction import preprocess_jobs
    preprocess_jobs.clear(ref_id)

    w = _db().update_work(
        ref_id,
        file_path=str(dest),
        has_full_text=True,
        preprocessing_status="pending",
        segments_json=json.dumps(state, ensure_ascii=False),
    )
    return w


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
        db = _db()
        work = db.get_work(ref_id)
        if not work:
            raise HTTPException(404, "参考作品不存在")
        # Ensure text is available: if work has file_path but source isn't file_upload, fix it
        if work.get("file_path") and work.get("source") not in ("file_upload", "platform_crawl"):
            db.update_work(ref_id, preprocessing_status="pending")
        from analysis.feature_extraction.pipeline import FeatureExtractionPipeline
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
        from analysis.feature_extraction.pipeline import FeatureExtractionPipeline
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
            from analysis.feature_extraction.chapter_parser import visible_char_count
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
        from analysis.feature_extraction.pipeline import FeatureExtractionPipeline
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
        from analysis.feature_extraction.pipeline import FeatureExtractionPipeline
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
    from analysis.feature_extraction import preprocess_jobs
    from analysis.feature_extraction.pipeline import _load_chapter_patterns
    db = _db()
    w = db.get_work(ref_id)
    if not w:
        raise HTTPException(404, "参考作品不存在")
    file_path = w.get("file_path")
    if not file_path:
        raise HTTPException(400, "尚未上传正文")
    extras = _load_chapter_patterns()
    job = await preprocess_jobs.start_guess_job_for_path(
        ref_id, file_path, extra_patterns=extras,
    )
    return job.to_status()


@router.get("/works/{ref_id}/preprocess/guess_status")
def preprocess_guess_status(ref_id: str):
    """Return the live status of the format-matching job: progress
    (current_pattern / total_patterns), the candidate list once done,
    and the suggested winner."""
    from analysis.feature_extraction import preprocess_jobs
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
    from analysis.feature_extraction import preprocess_jobs
    from analysis.feature_extraction.pipeline import _load_chapter_patterns
    db = _db()
    w = db.get_work(ref_id)
    if not w:
        raise HTTPException(404, "参考作品不存在")
    file_path = w.get("file_path")
    if not file_path:
        raise HTTPException(400, "尚未上传正文")
    extras = _load_chapter_patterns()
    multi_list = [s.strip() for s in (force_patterns or "").split(",") if s.strip()] or None
    job = await preprocess_jobs.start_job_for_path(
        ref_id, file_path, extra_patterns=extras,
        force_pattern=force_pattern, force_patterns=multi_list,
    )
    return job.to_status()


class ChapterContentEdit(BaseModel):
    content: str


@router.get("/works/{ref_id}/preprocess/chapter/{number}/content")
def get_chapter_content(ref_id: str, number: int):
    """Return the FULL content of one chapter. Used by the inline editor
    so the user can trim a tail "求月票" aside without removing the whole
    chapter."""
    from analysis.feature_extraction.chapter_parser import (
        detect_chapters, visible_char_count,
    )
    from analysis.feature_extraction.pipeline import (
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
    result = detect_chapters(text, extra_patterns=extras)
    for c in result["chapters"]:
        if c["number"] == number:
            return {
                "number": number,
                "title": c["title"],
                "content": c["content"],
                "char_count": visible_char_count(c["content"]),
            }
    raise HTTPException(404, f"未找到第 {number} 章")


@router.patch("/works/{ref_id}/preprocess/chapter/{number}/content")
def patch_chapter_content(ref_id: str, number: int, body: ChapterContentEdit):
    """Replace a chapter's body with ``content``. Snapshots the file to
    ``{path}.bak`` first (overwriting any earlier backup) so the user
    can undo via the existing undo_exclusions endpoint. Other chapters
    are left untouched."""
    from analysis.feature_extraction.chapter_parser import (
        detect_chapters, replace_chapter_content, visible_char_count,
    )
    from analysis.feature_extraction.pipeline import (
        FeatureExtractionPipeline, _load_chapter_patterns,
    )
    from analysis.feature_extraction import preprocess_jobs
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
    # Update the persisted preprocess result so the chapter list reflects
    # the new content / char count without re-running detection.
    try:
        state = json.loads(w.get("segments_json") or "{}")
    except Exception:
        state = {}
    if isinstance(state, dict):
        pre = state.get("preprocess") or {}
        chapters_list = pre.get("chapters") or []
        from analysis.feature_extraction.chapter_parser import make_preview
        for c in chapters_list:
            if c.get("number") == number:
                c["char_count"] = visible_char_count(body.content)
                pv = make_preview(body.content)
                c["preview_head"] = pv["head"]
                c["preview_tail"] = pv["tail"]
                break
        pre["chapters"] = chapters_list
        state["preprocess"] = pre
        state["exclusion_backup"] = {
            "path": str(bak),
            "removed_chapters": [],
            "prev_char_count": len(text),
            "edited_chapter": number,
        }
        # Clear segment plan / completed results since text changed
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

def _chapter_patterns_path():
    from pathlib import Path
    return Path(__file__).resolve().parents[4] / "data" / "settings.json"


def _read_settings_dict() -> dict:
    p = _chapter_patterns_path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def _write_settings_dict(d: dict) -> None:
    p = _chapter_patterns_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")


@router.get("/chapter_patterns")
def get_chapter_patterns():
    """Return the user's custom chapter patterns. Each entry:
    ``{name: str, regex: str, enabled: bool}``. The regex should capture
    two groups: (number, title). Title group may be omitted."""
    data = _read_settings_dict()
    raw = data.get("chapter_patterns")
    return {"patterns": raw if isinstance(raw, list) else []}


class ChapterPatternsBody(BaseModel):
    patterns: list[dict]


@router.put("/chapter_patterns")
def put_chapter_patterns(body: ChapterPatternsBody):
    """Replace the entire custom-pattern list. Each entry uses either
    ``format`` (user-friendly template, preferred) or ``regex`` (advanced).
    Validates regex compilation before saving."""
    import re as _re
    from analysis.feature_extraction.chapter_parser import format_to_regex
    cleaned: list[dict] = []
    for i, p in enumerate(body.patterns or []):
        if not isinstance(p, dict):
            raise HTTPException(400, f"第 {i + 1} 项格式错误")
        fmt = (p.get("format") or "").strip()
        regex = (p.get("regex") or "").strip()
        if not fmt and not regex:
            continue
        # Per user request: when no explicit name, use the format text
        # itself as the name (the format IS the identifier).
        name = (p.get("name") or "").strip() or fmt or f"自定义 {i + 1}"
        # Validate by compiling the effective regex
        effective = regex or format_to_regex(fmt)
        try:
            _re.compile(effective)
        except _re.error as e:
            raise HTTPException(400, f"「{name}」格式无效：{e}")
        entry: dict = {"name": name, "enabled": bool(p.get("enabled", True))}
        if fmt:
            entry["format"] = fmt
        if regex:
            entry["regex"] = regex
        cleaned.append(entry)
    data = _read_settings_dict()
    data["chapter_patterns"] = cleaned
    _write_settings_dict(data)
    return {"patterns": cleaned}


@router.delete("/chapter_patterns/{name}")
def delete_chapter_pattern(name: str):
    """Delete a custom chapter pattern by its saved name (URL-encoded).
    Built-in patterns can't be deleted via this endpoint."""
    data = _read_settings_dict()
    raw = data.get("chapter_patterns")
    if not isinstance(raw, list):
        raise HTTPException(404, f"未找到格式「{name}」")
    before = len(raw)
    cleaned = [p for p in raw if isinstance(p, dict) and (p.get("name") or "").strip() != name]
    if len(cleaned) == before:
        raise HTTPException(404, f"未找到格式「{name}」（内置格式不可删除）")
    data["chapter_patterns"] = cleaned
    _write_settings_dict(data)
    return {"patterns": cleaned, "deleted": name}


class ChapterPatternTestBody(BaseModel):
    regex: str | None = None
    format: str | None = None  # user-friendly template ("第N章", "N、", etc.)
    pattern_name: str | None = None  # look up by name (built-in or custom)
    ref_id: str | None = None
    sample_text: str | None = None


@router.post("/chapter_patterns/test")
def test_chapter_pattern(body: ChapterPatternTestBody):
    """Compile + run a candidate pattern against either the given
    sample text or the (capped) full text of a specific work. Accepts
    either:
      - ``pattern_name``: looks up a built-in or custom pattern by name
        (used by the format-confirm panel's per-row 测试 button).
      - ``format``: user-friendly template (e.g. "第N章").
      - ``regex``: raw regex (advanced).
    Capped at 2 MB scanned text for speed."""
    import re as _re
    from analysis.feature_extraction.chapter_parser import (
        format_to_regex, _PATTERNS as BUILTIN, _compile_extra,
    )
    from analysis.feature_extraction.pipeline import _load_chapter_patterns
    regex = (body.regex or "").strip()
    fmt = (body.format or "").strip()
    pname = (body.pattern_name or "").strip()
    pat = None
    if pname:
        # Built-in first
        for n, p in BUILTIN:
            if n == pname:
                pat = p
                break
        # Then custom
        if pat is None:
            for n, p in _compile_extra(_load_chapter_patterns()):
                if n == pname:
                    pat = p
                    break
        if pat is None:
            raise HTTPException(404, f"未找到格式「{pname}」")
    else:
        if fmt and not regex:
            regex = format_to_regex(fmt)
        if not regex:
            raise HTTPException(400, "请提供 pattern_name / format / regex")
        try:
            pat = _re.compile(regex, _re.MULTILINE | _re.IGNORECASE)
        except _re.error as e:
            raise HTTPException(400, f"正则编译失败：{e}")
    if body.sample_text:
        text = body.sample_text
    elif body.ref_id:
        db = _db()
        w = db.get_work(body.ref_id)
        if not w:
            raise HTTPException(404, "参考作品不存在")
        from analysis.feature_extraction.pipeline import FeatureExtractionPipeline
        pipe = FeatureExtractionPipeline(db.db_path)
        text = pipe._load_text(w) or ""
    else:
        raise HTTPException(400, "请提供 ref_id 或 sample_text")
    # Cap test scan at 2 MB so quick-test stays fast on large works.
    # That's plenty to verify a format works — count saturates well
    # before this.
    MAX_TEST_SCAN = 2_000_000
    truncated = len(text) > MAX_TEST_SCAN
    scan_text = text[:MAX_TEST_SCAN] if truncated else text
    ms = list(pat.finditer(scan_text))
    preview = []
    for m in ms[:8]:
        preview.append({
            "match": m.group(0)[:60],
            "groups": [g for g in m.groups()[:2]],
            "pos": m.start(),
        })
    return {
        "count": len(ms), "preview": preview,
        "scanned_chars": len(scan_text), "truncated": truncated,
    }


@router.post("/works/{ref_id}/preprocess/pause")
async def preprocess_pause(ref_id: str):
    from analysis.feature_extraction import preprocess_jobs
    ok = preprocess_jobs.pause_job(ref_id)
    if not ok:
        raise HTTPException(400, "当前无运行中的预处理任务")
    return {"ok": True, "state": "paused"}


@router.post("/works/{ref_id}/preprocess/resume")
async def preprocess_resume(ref_id: str):
    from analysis.feature_extraction import preprocess_jobs
    ok = preprocess_jobs.resume_job(ref_id)
    if not ok:
        raise HTTPException(400, "当前无暂停的预处理任务")
    return {"ok": True, "state": "running"}


@router.post("/works/{ref_id}/preprocess/cancel")
async def preprocess_cancel(ref_id: str):
    from analysis.feature_extraction import preprocess_jobs
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
    from analysis.feature_extraction.chapter_parser import (
        detect_chapters, _PATTERNS as BUILTIN, _compile_extra,
    )
    from analysis.feature_extraction.pipeline import (
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
    from analysis.feature_extraction import preprocess_jobs
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
            from analysis.feature_extraction.chapter_parser import visible_char_count
            out["chapters"] = [
                {**{k: v for k, v in c.items() if k != "content"},
                 "char_count": visible_char_count(c.get("content") or "")}
                for c in job.chapters
            ]
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
        return {
            "state": "done",
            "current_chapter": pre.get("total_chapters") or 0,
            "total_chapters": pre.get("total_chapters") or 0,
            "flagged_count": pre.get("flagged_count") or 0,
            "log": [],
            "chapters": pre.get("chapters") or [],
            "persisted": True,
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
    excluded_chapters: list[int]


@router.post("/works/{ref_id}/preprocess/apply_exclusions")
def preprocess_apply_exclusions(ref_id: str, body: ApplyExclusionsRequest):
    """Physically delete the excluded chapters from the on-disk text and
    re-save. The pre-edit text is saved to ``{file_path}.bak`` (overwriting
    any prior backup) so the user can undo via POST /preprocess/undo_exclusions
    while the backup still exists."""
    from analysis.feature_extraction import preprocess_jobs
    from analysis.feature_extraction.chapter_parser import (
        detect_chapters, apply_exclusions,
    )
    from analysis.feature_extraction.pipeline import (
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
    detect = detect_chapters(text, extra_patterns=_load_chapter_patterns())
    excluded = set(int(n) for n in (body.excluded_chapters or []))
    new_text = apply_exclusions(text, detect["chapters"], excluded)
    if not new_text.strip():
        raise HTTPException(400, "排除后文本为空，操作已取消")
    file_path = w.get("file_path")
    if not file_path:
        raise HTTPException(400, "作品没有关联的文件路径")
    src = Path(file_path)
    bak = src.with_suffix(src.suffix + ".bak")
    try:
        # Snapshot the pre-edit file so we can restore on undo. Overwrites
        # any prior .bak — only the most-recent apply is undoable.
        bak.write_text(text, encoding="utf-8")
        src.write_text(new_text, encoding="utf-8")
    except Exception as e:
        raise HTTPException(500, f"写入文件失败：{e}")
    try:
        state = json.loads(w.get("segments_json") or "{}")
    except Exception:
        state = {}
    if isinstance(state, dict):
        state.pop("preprocess", None)
        state.pop("custom_plan", None)
        state.pop("plan", None)
        state["results"] = {}
        state["completed"] = []
        # Track the undo backup so /undo_exclusions can find it.
        state["exclusion_backup"] = {
            "path": str(bak),
            "removed_chapters": sorted(excluded),
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
        "removed_chapters": sorted(excluded),
        "new_char_count": len(new_text),
        "can_undo": True,
    }


@router.get("/works/{ref_id}/preprocess/aside_paragraphs")
def preprocess_aside_paragraphs(ref_id: str):
    """Return author-aside PARAGRAPHS detected inside regular chapters
    (short blocks containing 求月票 / 求订阅 / 推荐票 / 感谢 / … that the
    user wants stripped from chapter bodies WITHOUT removing the whole
    chapter). Whole-chapter author entries (作者说章节 pattern) are not
    returned here — they have their own bulk-clean modal."""
    from analysis.feature_extraction.chapter_parser import (
        detect_chapters, detect_aside_paragraphs,
    )
    from analysis.feature_extraction.pipeline import (
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
    asides = detect_aside_paragraphs(result["chapters"])
    return {"asides": asides, "total_chapters": len(result["chapters"])}


class CleanAsideParagraphsBody(BaseModel):
    paragraphs: list[dict]  # [{chapter_number, para_index}]


@router.post("/works/{ref_id}/preprocess/clean_aside_paragraphs")
def preprocess_clean_aside_paragraphs(ref_id: str, body: CleanAsideParagraphsBody):
    """Remove the specified paragraphs from their chapters and rewrite
    the file. Snapshots the original to .bak (same undo path as
    apply_exclusions). Other paragraphs in those chapters are kept."""
    from analysis.feature_extraction.chapter_parser import (
        detect_chapters, apply_aside_paragraph_cleanup,
    )
    from analysis.feature_extraction.pipeline import (
        FeatureExtractionPipeline, _load_chapter_patterns,
    )
    from analysis.feature_extraction import preprocess_jobs
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
    if isinstance(state, dict):
        state.pop("preprocess", None)
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


@router.post("/works/{ref_id}/preprocess/undo_exclusions")
def preprocess_undo_exclusions(ref_id: str):
    """Restore the pre-apply text from the most recent .bak snapshot.
    Single-level undo — the next /apply_exclusions overwrites the backup,
    so undo only reaches back one step."""
    from analysis.feature_extraction import preprocess_jobs
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
        from analysis.feature_extraction.pipeline import FeatureExtractionPipeline
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
        from analysis.feature_extraction.pipeline import FeatureExtractionPipeline
        pipe = FeatureExtractionPipeline(db.db_path)
        return pipe.suggest_auto_plan(ref_id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(500, f"自动检测失败: {e}")



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


@router.post("/works/{ref_id}/segments/preview")
async def preview_segment(ref_id: str, body: SegmentRunRequest):
    """Run extraction for one segment WITHOUT persisting. Returns the full
    extracted payload so the user can review before committing."""
    db = _db()
    w = db.get_work(ref_id)
    if not w:
        raise HTTPException(404, "参考作品不存在")
    try:
        from analysis.feature_extraction.pipeline import FeatureExtractionPipeline
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


@router.post("/works/{ref_id}/segments/commit")
def commit_segment(ref_id: str, body: SegmentCommitRequest):
    """Persist a previewed (possibly user-edited) segment result."""
    db = _db()
    w = db.get_work(ref_id)
    if not w:
        raise HTTPException(404, "参考作品不存在")
    try:
        from analysis.feature_extraction.pipeline import FeatureExtractionPipeline
        pipe = FeatureExtractionPipeline(db.db_path)
        return pipe.persist_segment(ref_id, body.result)
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
        from analysis.feature_extraction.pipeline import FeatureExtractionPipeline
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
        from analysis.feature_extraction.pipeline import FeatureExtractionPipeline
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
- settings 项形态 {category, title, content, hidden?}。category 必须为 power_system/factions/geography/social_rules/history/hard_rules/worldview/other 之一。"""


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
        from models.router import ModelRouter
        from models.base import LLMMessage
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
        from analysis.feature_extraction.prompts import render as _render_prompt
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
        from analysis.feature_extraction.pipeline import FeatureExtractionPipeline
        from analysis.feature_extraction.narrative_extractor import (
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


# ═══ LoRA Training ══════════════════════════════════════

import asyncio
import logging

_lora_logger = logging.getLogger("inkoctobot.ui.backend.lora_training")
_lora_status: dict = {"status": "idle"}


class LoRATrainRequest(BaseModel):
    work_ids: list[str]
    base_model: str = "Qwen/Qwen2-1.5B"
    rank: int = 16
    alpha: int = 32
    epochs: int = 3
    learning_rate: float = 2e-4
    use_4bit: bool = True


@router.post("/lora/train")
async def start_lora_training(body: LoRATrainRequest):
    global _lora_status
    if _lora_status.get("status") == "running":
        raise HTTPException(409, "训练任务已在进行中")
    if not body.work_ids:
        raise HTTPException(400, "请至少选择一个参考作品")

    _lora_status = {
        "status": "running",
        "work_ids": body.work_ids,
        "progress": "初始化...",
        "error": None,
    }
    asyncio.create_task(_run_lora_training(body))
    return {"status": "started", "work_ids": body.work_ids}


async def _run_lora_training(body: LoRATrainRequest):
    global _lora_status
    try:
        import tempfile
        from preprocessing.lora.data_constructor import construct_sft_data, save_dataset
        from preprocessing.lora.quality_filter import filter_samples
        from preprocessing.lora.trainer import train_lora, LoRATrainConfig

        db = _db()
        all_samples = []
        _lora_status["progress"] = f"正在为 {len(body.work_ids)} 个作品构造训练数据..."

        for ref_id in body.work_ids:
            work = db.get_work(ref_id)
            if not work:
                continue
            # Get full text from file
            file_path = work.get("file_path", "")
            if not file_path or not Path(file_path).exists():
                continue
            text = Path(file_path).read_text("utf-8", errors="replace")
            # Simple chapter splitting by double newlines
            paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
            chapters = [{"content": p, "title": f"段落{i+1}", "index": i} for i, p in enumerate(paragraphs) if len(p) > 100]

            style_fp = None
            if work.get("style_fingerprint_json"):
                try:
                    style_fp = json.loads(work["style_fingerprint_json"])
                except Exception:
                    pass

            samples = construct_sft_data(
                chapters, task_type="style_transfer",
                style_fingerprint=style_fp,
                metadata={"ref_id": ref_id, "title": work.get("title", "")},
            )
            all_samples.extend(samples)

        if not all_samples:
            _lora_status = {"status": "error", "error": "没有可用的训练数据。请确保参考作品有上传全文。"}
            return

        _lora_status["progress"] = f"质量过滤 {len(all_samples)} 个样本..."
        filtered = filter_samples(all_samples)
        passed = filtered.passed if hasattr(filtered, "passed") else all_samples

        if not passed:
            _lora_status = {"status": "error", "error": "所有样本都被过滤掉了"}
            return

        _lora_status["progress"] = f"保存 {len(passed)} 个样本到数据集..."
        with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False, mode="w") as f:
            dataset_path = f.name
        save_dataset(passed, dataset_path)

        output_dir = str(settings.repo_root / "data" / "lora_output")
        Path(output_dir).mkdir(parents=True, exist_ok=True)

        config = LoRATrainConfig(
            base_model=body.base_model,
            rank=body.rank,
            alpha=body.alpha,
            epochs=body.epochs,
            learning_rate=body.learning_rate,
            use_4bit=body.use_4bit,
        )

        _lora_status["progress"] = "开始 LoRA 训练..."

        # Run training in executor to not block event loop
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, train_lora, config, dataset_path, output_dir)

        _lora_status = {
            "status": "done",
            "result": result,
            "progress": "训练完成！",
            "samples_used": len(passed),
        }

    except Exception as e:
        _lora_logger.error("LoRA training error: %s", e, exc_info=True)
        _lora_status = {"status": "error", "error": str(e)[:500]}


@router.get("/lora/status")
def lora_training_status():
    return _lora_status

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
    from analysis.feature_extraction.prompts import list_keys
    return {"items": list_keys()}


@router.get("/prompts/{key}")
def get_prompt(key: str):
    """Return the factory default + the current (possibly overridden) text."""
    from analysis.feature_extraction.prompts import (
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
    from analysis.feature_extraction.prompts import (
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
    from analysis.feature_extraction.prompts import DEFAULT_PROMPTS, render
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
):
    """Render the prompt with the real `vars` for a specific upcoming call.

    For segment-scoped prompts (characters/settings/rhythm/chat_system),
    the server loads the work, builds the segment text and splices it in,
    so the UI shows EXACTLY what the model will see.
    """
    from analysis.feature_extraction.prompts import DEFAULT_PROMPTS, render, get_template
    if key not in DEFAULT_PROMPTS:
        raise HTTPException(404, f"unknown prompt key: {key}")

    template = get_template(key)
    entry = DEFAULT_PROMPTS[key]
    required_vars = list(entry.get("vars") or [])

    # If the prompt has no vars (e.g. chat_system), return as-is
    if not required_vars:
        return {"key": key, "template": template, "rendered": template, "vars": {}}

    # Segment-scoped: need ref_id + segment_index → build chapter text
    if key in {"reference.characters", "reference.settings", "reference.rhythm"}:
        if not ref_id or segment_index is None:
            raise HTTPException(400, "ref_id + segment_index required for this prompt")
        db = _db()
        w = db.get_work(ref_id)
        if not w:
            raise HTTPException(404, "参考作品不存在")
        try:
            from analysis.feature_extraction.pipeline import FeatureExtractionPipeline
            pipe = FeatureExtractionPipeline(db.db_path)
            text = pipe._load_text(w)
            if not text:
                raise HTTPException(400, "作品尚未上传正文")
            all_chapters = pipe._split_chapters(text)
            plan = pipe.plan_segments(all_chapters)
            segs = plan["segments"]
            if segment_index < 0 or segment_index >= len(segs):
                raise HTTPException(400, "segment_index 超出范围")
            seg = segs[segment_index]
            seg_chapters = [
                all_chapters[j - 1]
                for j in range(seg["start_chapter"], seg["end_chapter"] + 1)
            ]
            from analysis.feature_extraction.ai_extractor import _build_segment_text
            seg_text, nchars = _build_segment_text(seg_chapters)
            vars_ = {
                "n_chapters": len(seg_chapters),
                "n_chars": nchars,
                "text": seg_text,
            }
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(500, f"构建预览失败: {e}")
        rendered = render(key, **vars_)
        return {"key": key, "template": template, "rendered": rendered, "vars": vars_}

    # ai_complete: ref_id is enough
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

    # Fallback: render with empty vars (will probably raise)
    rendered = render(key)
    return {"key": key, "template": template, "rendered": rendered, "vars": {}}


@router.get("/web_search/capability")
def web_search_capability():
    """Return whether the configured ``reference_web_search`` role's
    provider+model is in the known web-search-capable set."""
    try:
        from models.router import ModelRouter
        from models.web_search_capabilities import supports_web_search, describe
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
        from models.router import ModelRouter
        from models.web_search_capabilities import supports_web_search, describe
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
    from analysis.feature_extraction.prompts import render as _render_prompt
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


# ═══ Vector index + similarity search ═════════════════════════════

class IndexRunRequest(BaseModel):
    level: str = "all"   # 'L1' | 'L2' | 'L3' | 'all' (= L1 + L2)
    include_l3: bool = False


def _indexer():
    """Lazily build a WorkIndexer using the configured embedding backend.
    Raises HTTPException(503) with a clear message if any dep is missing."""
    try:
        from rag.work_index import make_indexer
        db = _db()
        return make_indexer(db.db_path)
    except ImportError as e:
        raise HTTPException(503, f"向量索引依赖缺失：{e}")
    except Exception as e:
        raise HTTPException(500, f"索引器初始化失败：{e}")


@router.post("/works/{ref_id}/index/run")
async def run_work_index(ref_id: str, body: IndexRunRequest):
    """Build / refresh the vector index for one work. L1+L2 are cheap and
    finish in-line; L3 is heavier but resumable (progress is persisted to
    work_index_progress so the call can be killed and restarted)."""
    db = _db()
    w = db.get_work(ref_id)
    if not w:
        raise HTTPException(404, "参考作品不存在")
    indexer = _indexer()
    try:
        if body.level == "L1":
            return await indexer.index_l1(ref_id)
        if body.level == "L2":
            return await indexer.index_l2(ref_id)
        if body.level == "L3":
            return await indexer.index_l3(ref_id)
        # default: all
        return await indexer.index_all(ref_id, include_l3=body.include_l3)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(500, f"索引失败：{e}")


@router.get("/works/{ref_id}/index/progress")
def get_index_progress(ref_id: str):
    """Per-level progress (L1/L2/L3) for resumable indexing UI."""
    indexer = _indexer()
    return {"items": indexer.get_progress(ref_id)}


@router.delete("/works/{ref_id}/index")
def clear_work_index(ref_id: str, level: Optional[str] = None):
    """Drop a work's vectors (optionally limit to one level)."""
    indexer = _indexer()
    levels = [level] if level else None
    indexer.clear_work(ref_id, levels=levels)
    return {"ok": True}


@router.get("/search")
async def search_works(
    q: str = Query(..., description="自然语言查询"),
    k: int = Query(10, ge=1, le=50),
    levels: str = Query("L1,L2", description="L1,L2 (默认) 或 L3 (单作品深度搜索)"),
    ref_id: Optional[str] = None,
):
    """Two-stage retrieval. Default is Stage 1 (L1+L2 across all works).
    Pass ``levels=L3&ref_id=...`` to drill into one work's raw chunks."""
    indexer = _indexer()
    level_list = [s.strip() for s in (levels or "L1,L2").split(",") if s.strip()]
    if "L3" in level_list and not ref_id:
        raise HTTPException(400, "L3 深度搜索需要指定 ref_id")
    try:
        hits = await indexer.search(q, k=k, levels=level_list, ref_id=ref_id)
        return {"q": q, "k": k, "levels": level_list, "hits": hits}
    except Exception as e:
        raise HTTPException(500, f"搜索失败：{e}")
