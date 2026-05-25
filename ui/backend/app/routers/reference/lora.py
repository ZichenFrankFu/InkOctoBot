"""LoRA fine-tuning trigger + status.

Owns one piece of module-level state (``_lora_status``) since training
is a single-tenant background job. The two endpoints simply start a
job or poll its status; the heavy lifting is in ``reference_ingest.lora``.
"""
from __future__ import annotations

import asyncio
import json
import logging
import tempfile
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ...settings import settings
from ._common import db

router = APIRouter()

_logger = logging.getLogger("inkoctobot.ui.backend.lora_training")
_status: dict = {"status": "idle"}


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
    global _status
    if _status.get("status") == "running":
        raise HTTPException(409, "训练任务已在进行中")
    if not body.work_ids:
        raise HTTPException(400, "请至少选择一个参考作品")

    _status = {
        "status": "running",
        "work_ids": body.work_ids,
        "progress": "初始化...",
        "error": None,
    }
    asyncio.create_task(_run_lora_training(body))
    return {"status": "started", "work_ids": body.work_ids}


async def _run_lora_training(body: LoRATrainRequest):
    global _status
    try:
        from reference_ingest.lora.data_constructor import construct_sft_data, save_dataset
        from reference_ingest.lora.quality_filter import filter_samples
        from reference_ingest.lora.trainer import train_lora, LoRATrainConfig

        rdb = db()
        all_samples = []
        _status["progress"] = f"正在为 {len(body.work_ids)} 个作品构造训练数据..."

        for ref_id in body.work_ids:
            work = rdb.get_work(ref_id)
            if not work:
                continue
            file_path = work.get("file_path", "")
            if not file_path or not Path(file_path).exists():
                continue
            text = Path(file_path).read_text("utf-8", errors="replace")
            paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
            chapters = [{"content": p, "title": f"段落{i+1}", "index": i}
                        for i, p in enumerate(paragraphs) if len(p) > 100]

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
            _status = {"status": "error",
                       "error": "没有可用的训练数据。请确保参考作品有上传全文。"}
            return

        _status["progress"] = f"质量过滤 {len(all_samples)} 个样本..."
        filtered = filter_samples(all_samples)
        passed = filtered.passed if hasattr(filtered, "passed") else all_samples

        if not passed:
            _status = {"status": "error", "error": "所有样本都被过滤掉了"}
            return

        _status["progress"] = f"保存 {len(passed)} 个样本到数据集..."
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

        _status["progress"] = "开始 LoRA 训练..."
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, train_lora, config, dataset_path, output_dir)

        _status = {
            "status": "done",
            "result": result,
            "progress": "训练完成！",
            "samples_used": len(passed),
        }

    except Exception as e:
        _logger.error("LoRA training error: %s", e, exc_info=True)
        _status = {"status": "error", "error": str(e)[:500]}


@router.get("/lora/status")
def lora_training_status():
    return _status
