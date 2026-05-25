"""
LoRA training data construction.

Builds instruction-following SFT datasets from reference works stored in
the reference database.  Output format is JSONL compatible with
HuggingFace ``datasets``.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger("inkoctobot.reference_ingest.lora.data_constructor")

# ── Task-type templates ──

_TEMPLATES: dict[str, dict[str, str]] = {
    "style_transfer": {
        "system": "你是一位专业的中文网络小说作家。请按照指定的风格要求改写以下内容。",
        "instruction": "请将以下场景描述改写为目标风格的小说文本：",
    },
    "continuation": {
        "system": "你是一位专业的中文网络小说作家。请根据上文续写后续内容。",
        "instruction": "请根据以下上文，续写接下来的内容（约{length}字）：",
    },
    "editing": {
        "system": "你是一位专业的中文小说编辑。请对以下文本进行润色修改，提升文学质量。",
        "instruction": "请润色以下小说片段，保持原意不变但提升表达质量：",
    },
}


def construct_sft_data(
    chapters: list[dict[str, Any]],
    *,
    task_type: str = "style_transfer",
    chunk_size: int = 1500,
    overlap: int = 200,
    style_fingerprint: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Build SFT training samples from chapter dicts.

    Parameters
    ----------
    chapters : list[dict]
        Each dict has ``"content"`` and optionally ``"title"`` / ``"index"``.
    task_type : str
        One of ``"style_transfer"``, ``"continuation"``, ``"editing"``.
    chunk_size : int
        Target character length per training sample.
    overlap : int
        Overlap between consecutive chunks (for continuation context).
    style_fingerprint : dict, optional
        Style profile metadata to attach to each sample.
    metadata : dict, optional
        Extra metadata to include in each sample.

    Returns
    -------
    list[dict]
        Each dict: ``{"instruction", "input", "output", "text", "metadata"}``.
    """
    tmpl = _TEMPLATES.get(task_type, _TEMPLATES["style_transfer"])
    samples: list[dict[str, Any]] = []

    for ch in chapters:
        content = ch.get("content", "")
        if not content:
            continue

        chunks = _chunk_text(content, chunk_size, overlap)

        for i, chunk in enumerate(chunks):
            if len(chunk.strip()) < 50:
                continue

            sample_meta = {
                "task_type": task_type,
                "chapter_title": ch.get("title", ""),
                "chapter_index": ch.get("index", 0),
                "chunk_index": i,
                **(metadata or {}),
            }
            if style_fingerprint:
                sample_meta["style_fingerprint"] = style_fingerprint

            if task_type == "continuation" and i > 0:
                context = chunks[i - 1][-overlap:] if i > 0 else ""
                instruction = tmpl["instruction"].format(length=len(chunk))
                sample = {
                    "instruction": instruction,
                    "input": context,
                    "output": chunk,
                    "text": chunk,
                    "metadata": sample_meta,
                }
            elif task_type == "editing":
                sample = {
                    "instruction": tmpl["instruction"],
                    "input": chunk,
                    "output": chunk,  # self-reconstruction baseline
                    "text": chunk,
                    "metadata": sample_meta,
                }
            else:  # style_transfer (default)
                scene_desc = _extract_scene_desc(chunk)
                sample = {
                    "instruction": tmpl["instruction"],
                    "input": scene_desc,
                    "output": chunk,
                    "text": chunk,
                    "metadata": sample_meta,
                }

            samples.append(sample)

    logger.info("Constructed %d SFT samples (task=%s)", len(samples), task_type)
    return samples


def save_dataset(
    samples: list[dict[str, Any]],
    output_path: str | Path,
    *,
    format: str = "jsonl",
) -> None:
    """Save samples to disk.

    Parameters
    ----------
    format : str
        ``"jsonl"`` (default) or ``"json"``.
    """
    p = Path(output_path)
    p.parent.mkdir(parents=True, exist_ok=True)

    if format == "json":
        with open(p, "w", encoding="utf-8") as f:
            json.dump(samples, f, ensure_ascii=False, indent=2)
    else:
        with open(p, "w", encoding="utf-8") as f:
            for s in samples:
                f.write(json.dumps(s, ensure_ascii=False) + "\n")

    logger.info("Saved %d samples to %s", len(samples), p)


# ── Internal helpers ──

def _chunk_text(text: str, size: int, overlap: int) -> list[str]:
    """Split text into overlapping chunks of roughly *size* characters."""
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = start + size
        # Try to break on sentence boundary
        if end < len(text):
            for sep in ("。", "！", "？", "\n"):
                idx = text.rfind(sep, start + size // 2, end + 200)
                if idx > start:
                    end = idx + 1
                    break
        chunks.append(text[start:end])
        start = end - overlap if end < len(text) else len(text)
    return chunks


def _extract_scene_desc(text: str) -> str:
    """Extract a short scene description from a text chunk (first 2 sentences)."""
    import re
    sents = re.split(r'[。！？]+', text)
    sents = [s.strip() for s in sents if len(s.strip()) > 5]
    return "。".join(sents[:2]) + "。" if sents else text[:100]
