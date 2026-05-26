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


# ─── Constitutional DPO (InkOS B3) ─────────────────────────────────


def construct_dpo_data(
    triples: list[dict[str, Any]],
    *,
    max_length: int = 2048,
    metadata: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Build DPO preference pairs from (prompt, rejected, chosen) triples.

    InkOS Weaver paper's DPO stage: use the evaluator-flagged AI draft
    as ``rejected`` and the user's hand-edit (or constitutional rewrite)
    as ``chosen``. The model learns to prefer the constitutional shape
    over the violating shape.

    Each input triple has::
        {
          "prompt":   str  — the request given to the writer
          "rejected": str  — AI output that violated some rule
          "chosen":   str  — corrected version (user edit / second pass)
          "rationale": str  — why chosen > rejected (optional)
          "violation_categories": list[str]  — e.g. ['list_syntax', 'pov_break']
        }

    Output rows use TRL's standard DPO schema:
        {prompt, chosen, rejected, metadata}

    Compatible with ``trl.DPOTrainer`` and HuggingFace ``datasets``.
    """
    out: list[dict[str, Any]] = []
    for t in triples:
        prompt = (t.get("prompt") or "").strip()
        rejected = (t.get("rejected") or "").strip()
        chosen = (t.get("chosen") or "").strip()
        if not (prompt and rejected and chosen):
            continue
        # Trivial pair (chosen == rejected) is a degenerate sample
        if rejected == chosen:
            continue
        # Truncate to max_length characters defensively — TRL tokenizer
        # truncates again at training time using model max_length.
        if len(prompt) > max_length:
            prompt = prompt[:max_length]
        if len(rejected) > max_length:
            rejected = rejected[:max_length]
        if len(chosen) > max_length:
            chosen = chosen[:max_length]
        sample_meta = {
            "rationale": t.get("rationale", ""),
            "violation_categories": t.get("violation_categories", []),
            "source_chapter_id": t.get("source_chapter_id", ""),
            "source_project_id": t.get("source_project_id", ""),
            **(metadata or {}),
        }
        out.append({
            "prompt": prompt,
            "chosen": chosen,
            "rejected": rejected,
            "metadata": sample_meta,
        })
    logger.info("Constructed %d DPO preference pairs", len(out))
    return out


def collect_dpo_pairs_from_db(
    db_path: str,
    project_id: str | None = None,
    *,
    min_diff_chars: int = 50,
) -> list[dict[str, Any]]:
    """Mine DPO triples from the text_versions table.

    Strategy: find chapters where source='ai' was followed by
    source='user_edit' (the user fixed something). Pair (ai_text, user_text)
    as (rejected, chosen). The "prompt" reconstructs from the chapter's
    outline + synopsis (best available approximation of what the Writer
    saw at generation time).

    The chapter's evaluation_json (if not empty) is parsed for issue
    categories so each pair is tagged with what was wrong — useful for
    weighting / curriculum during training.

    Returns triples ready for ``construct_dpo_data``.
    """
    import sqlite3
    import json as _j

    sql = """
        SELECT
            ch.chapter_id, ch.project_id, ch.outline, ch.synopsis,
            ch.title, ch.evaluation_json,
            ai_v.content AS ai_text,
            user_v.content AS user_text
        FROM chapters ch
        JOIN text_versions ai_v ON ai_v.chapter_id = ch.chapter_id
                                AND ai_v.source = 'ai'
        JOIN text_versions user_v ON user_v.chapter_id = ch.chapter_id
                                   AND user_v.source = 'user_edit'
                                   AND user_v.version_num > ai_v.version_num
        WHERE LENGTH(ai_v.content) > 0
          AND LENGTH(user_v.content) > 0
    """
    params: list[Any] = []
    if project_id:
        sql += " AND ch.project_id = ?"
        params.append(project_id)
    sql += " ORDER BY ch.chapter_num"

    triples: list[dict[str, Any]] = []
    try:
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(sql, params).fetchall()
    except sqlite3.OperationalError as e:
        logger.warning("DPO mining query failed: %s", e)
        return []

    for r in rows:
        ai_text = r["ai_text"] or ""
        user_text = r["user_text"] or ""
        if abs(len(ai_text) - len(user_text)) < min_diff_chars:
            # Edit too small to be a meaningful preference signal
            continue

        # Reconstruct an approximate prompt
        outline = r["outline"] or ""
        title = r["title"] or ""
        synopsis = r["synopsis"] or ""
        prompt_parts = []
        if title:
            prompt_parts.append(f"章节标题：{title}")
        if synopsis:
            prompt_parts.append(f"梗概：{synopsis}")
        if outline:
            prompt_parts.append(f"大纲：\n{outline}")
        prompt = "\n\n".join(prompt_parts) or "请根据上下文创作章节。"

        # Extract violation categories from evaluation_json if available
        violations: list[str] = []
        try:
            eval_data = _j.loads(r["evaluation_json"] or "{}")
            for issue in (eval_data.get("issues") or []):
                if isinstance(issue, dict):
                    cat = issue.get("type") or issue.get("category")
                    if cat:
                        violations.append(str(cat))
        except (TypeError, _j.JSONDecodeError):
            pass

        triples.append({
            "prompt": prompt,
            "rejected": ai_text,
            "chosen": user_text,
            "rationale": "user_edit replaced AI draft",
            "violation_categories": violations,
            "source_chapter_id": r["chapter_id"],
            "source_project_id": r["project_id"],
        })

    logger.info(
        "Mined %d DPO triples from %s",
        len(triples), f"project={project_id}" if project_id else "all projects",
    )
    return triples


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
