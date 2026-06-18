"""Prompt template registry endpoints.

CRUD on the user-overridable prompt templates managed by
``reference_pipeline.prompts``. Plus two preview surfaces:

  - ``/prompts/{key}/preview``         single-render with real work data
  - ``/prompts/{key}/preview_chunks``  segment-as-N-chunks preview for the
                                        copy-to-web-LLM workflow

Both preview endpoints are aware of which prompt keys are work-scoped vs.
segment-scoped vs. project-scoped, and pull the appropriate context so
the rendered prompt the user sees == the prompt the model will actually
receive at run time.
"""
from __future__ import annotations

import json
from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ._common import MEDIA_TYPE_ZH, db

router = APIRouter()


class PromptUpdateRequest(BaseModel):
    template: Optional[str] = None  # non-null = persist; null = reset to factory


class PromptPreviewRequest(BaseModel):
    vars: dict[str, Any] = {}
    override: Optional[str] = None


# ── CRUD ──────────────────────────────────────────────────────────


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
    from reference_pipeline.prompts import DEFAULT_PROMPTS, set_template, reset
    if key not in DEFAULT_PROMPTS:
        raise HTTPException(404, f"unknown prompt key: {key}")
    if body.template is None:
        reset(key)
    else:
        set_template(key, body.template)
    return get_prompt(key)


@router.post("/prompts/{key}/render")
def render_prompt(key: str, body: PromptPreviewRequest):
    """Render the prompt with explicit vars (used by the preview UI).

    The ``override`` field, if set, takes precedence over the persisted
    override for THIS call only — nothing is saved.
    """
    from reference_pipeline.prompts import DEFAULT_PROMPTS, render
    if key not in DEFAULT_PROMPTS:
        raise HTTPException(404, f"unknown prompt key: {key}")
    try:
        rendered = render(key, override=body.override, **(body.vars or {}))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"key": key, "rendered": rendered}


# ── Preview (real work context) ───────────────────────────────────


@router.get("/prompts/{key}/preview")
def preview_prompt(
    key: str,
    ref_id: Optional[str] = None,
    segment_index: Optional[int] = None,
    project_id: Optional[str] = None,
    chapter_id: Optional[str] = None,
    chapter_num: int = 1,
):
    """Render the prompt with the real ``vars`` for a specific upcoming call.

    For segment-scoped prompts (characters/settings/rhythm/chat_system),
    the server loads the work, builds the segment text and splices it in
    so the UI shows EXACTLY what the model will see. For
    ``generation.single_agent``, when ``project_id`` is given, the full
    RAG context is assembled so the preview matches what generation will
    use.
    """
    from reference_pipeline.prompts import DEFAULT_PROMPTS, render, get_template
    if key not in DEFAULT_PROMPTS:
        raise HTTPException(404, f"unknown prompt key: {key}")

    template = get_template(key)
    entry = DEFAULT_PROMPTS[key]
    required_vars = list(entry.get("vars") or [])

    if not required_vars:
        return {"key": key, "template": template, "rendered": template, "vars": {}}

    # Pure-setting: ref_id + optional chunk_index → pull quick_input,
    # split into chunks, render the requested chunk's prompt. Used by
    # the 网页版 mode's PromptCopyPanel so the user copies *exactly*
    # what the API path would send.
    if key == "reference.pure_setting":
        if not ref_id:
            raise HTTPException(400, "ref_id required for this prompt")
        from .pure_setting import _split_chunks
        rdb = db()
        w = rdb.get_work(ref_id)
        if not w:
            raise HTTPException(404, "参考作品不存在")
        text = (w.get("quick_input_text") or "").strip()
        if not text:
            raise HTTPException(400, "快捷输入为空 — 请先粘贴 wiki 条目原文")
        chunks = _split_chunks(text)
        ci = max(0, min(segment_index or 0, len(chunks) - 1))
        chunk_text = chunks[ci]["text"]
        vars_ = {
            "title": w.get("title", ""),
            "author": w.get("creator", "") or "",
            "chunk_index_human": ci + 1,
            "total_chunks": len(chunks),
            "n_chars": len(chunk_text),
            "text": chunk_text,
        }
        rendered = render(key, **vars_)
        return {"key": key, "template": template, "rendered": rendered, "vars": vars_}

    # Work-scoped: volume_detect just needs ref_id.
    if key == "reference.volume_detect":
        if not ref_id:
            raise HTTPException(400, "ref_id required for this prompt")
        rdb = db()
        w = rdb.get_work(ref_id)
        if not w:
            raise HTTPException(404, "参考作品不存在")
        try:
            from reference_pipeline.pipeline import FeatureExtractionPipeline
            pipe = FeatureExtractionPipeline(rdb.db_path)
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
        rdb = db()
        w = rdb.get_work(ref_id)
        if not w:
            raise HTTPException(404, "参考作品不存在")
        try:
            from reference_pipeline.pipeline import (
                FeatureExtractionPipeline, build_work_ctx,
            )
            from reference_pipeline.ai_extractor import _build_segment_text
            pipe = FeatureExtractionPipeline(rdb.db_path)
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
            seg_text, nchars = _build_segment_text(seg_chapters)
            ctx = build_work_ctx(w, seg, segment_index)
            vars_: dict[str, Any] = {
                **ctx,
                "n_chapters": len(seg_chapters),
                "n_chars": nchars,
                "text": seg_text,
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
        rdb = db()
        w = rdb.get_work(ref_id)
        if not w:
            raise HTTPException(404, "参考作品不存在")
        author_hint = (
            f"已知作者：{w['creator']}（可用作辅助检索；如有更准确的全名请覆盖）"
            if w.get("creator") else "作者未知，请通过标题检索"
        )
        vars_ = {
            "media_type_zh": MEDIA_TYPE_ZH.get(w.get("media_type", ""), "作品"),
            "title": w.get("title", ""),
            "author_hint": author_hint,
        }
        rendered = render(key, **vars_)
        return {"key": key, "template": template, "rendered": rendered, "vars": vars_}

    # generation.single_agent: RAG-assembled, chapter-scoped.
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

    # Fallback: render with placeholder vars so structure is visible.
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
    """Render the prompt for a segment as MULTIPLE chunks so the user can
    run an over-budget volume as N separate web-LLM calls instead of
    having content silently truncated.

    Only ``reference.outline`` benefits from per-chunk processing today
    (characters / settings ideally see the full text). For other keys
    this returns a single-chunk list.
    """
    from reference_pipeline.prompts import DEFAULT_PROMPTS, render, get_template
    if key not in DEFAULT_PROMPTS:
        raise HTTPException(404, f"unknown prompt key: {key}")
    if max_chars < 4_000 or max_chars > 200_000:
        raise HTTPException(400, "max_chars 必须在 4000–200000 之间")

    rdb = db()
    w = rdb.get_work(ref_id)
    if not w:
        raise HTTPException(404, "参考作品不存在")
    try:
        from reference_pipeline.pipeline import (
            FeatureExtractionPipeline, build_work_ctx,
        )
        from reference_pipeline.ai_extractor import build_segment_text_chunks
        pipe = FeatureExtractionPipeline(rdb.db_path)
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
            # reference.style / reference.unified treat {start/end_chapter}
            # as the CHUNK range (no separate chunk_* vars), so narrow them.
            if key in ("reference.style", "reference.unified"):
                vars_["start_chapter"] = ci["start_chapter"]
                vars_["end_chapter"] = ci["end_chapter"]
                vars_["n_chapters"] = ci["n_chapters"]
            try:
                rendered = render(key, **vars_)
            except ValueError as e:
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
