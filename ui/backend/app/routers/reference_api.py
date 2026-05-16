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
})


class AnalysisUpdate(BaseModel):
    field: str
    # Accept dicts or lists (characters is a list)
    data: Any


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