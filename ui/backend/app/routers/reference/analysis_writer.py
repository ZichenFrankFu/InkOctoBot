"""Analysis-result writers.

Endpoints that produce or update a work's analysis JSON fields
(plot_outline / chronicle / per-field analysis update). These all
operate on the same set of ``ReferenceWork.*_json`` columns and
share the prompt-rendering helpers from
``reference_pipeline.prompts``.
"""
from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ._common import ANALYSIS_FIELDS, db, strip_json_blob

router = APIRouter()


# ── Models ────────────────────────────────────────────────────────


class AnalysisUpdate(BaseModel):
    field: str
    data: Any  # Will be JSON-serialized for storage


class OutlineSummaryPromptRequest(BaseModel):
    ref_id: str
    segment_index: int
    # Flat list of event dicts (from one or more outline per-chunk runs).
    # Each dict needs at least name+description; we strip unknown keys
    # before embedding so the rendered prompt stays small.
    events: list[dict]


class OutlineGranularityRequest(BaseModel):
    level: str = "major_event"   # major_event | volume | book
    prompt_only: bool = False


# ── Plot-outline granularity table ────────────────────────────────


_OUTLINE_LEVELS = {
    "major_event": ("大事件级", "只保留推动主线与重要支线的关键事件，合并琐碎情节；事件总数约压缩到原来的三分之一。"),
    "volume": ("卷级", "每个 epoch（卷 / 大阶段）只保留 3-6 个里程碑级事件，periods 大幅合并。"),
    "book": ("全书级", "把整本书概括为 1 个 epoch、5-10 个事件，只呈现全书主干脉络。"),
}


def _build_granularity_prompt(plot: dict, level: str) -> str:
    """Render via the prompt registry so 设置 → 提示词 can override it."""
    from reference_pipeline.prompts import render as _render_prompt
    level_cn, level_hint = _OUTLINE_LEVELS[level]
    return _render_prompt(
        "reference.outline_granularity",
        level_cn=level_cn, level_hint=level_hint,
        plot_json=json.dumps(plot, ensure_ascii=False, indent=1),
    )


# ── Endpoints ─────────────────────────────────────────────────────


@router.post("/works/{ref_id}/plot_outline/extract")
def extract_plot_outline_only(ref_id: str):
    """Re-extract plot outline from chapter splits + existing narrative
    analysis. Useful for iterating on the outline without re-running the
    whole pipeline.
    """
    rdb = db()
    w = rdb.get_work(ref_id)
    if not w:
        raise HTTPException(404, "参考作品不存在")
    try:
        from reference_pipeline.pipeline import FeatureExtractionPipeline
        from reference_pipeline.narrative_extractor import (
            extract_narrative, extract_plot_outline,
        )
        pipe = FeatureExtractionPipeline(rdb.db_path)
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
        return rdb.update_work(ref_id, plot_outline_json=json.dumps(plot, ensure_ascii=False))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"剧情大纲提取失败: {e}")


@router.put("/works/{ref_id}/analysis")
def update_analysis(ref_id: str, body: AnalysisUpdate):
    """Replace a single analysis JSON field's value (one of
    ``ANALYSIS_FIELDS``). Used by the inline editors in the Analysis tab.
    """
    if body.field not in ANALYSIS_FIELDS:
        raise HTTPException(
            400, f"无效字段: {body.field}。允许: {', '.join(sorted(ANALYSIS_FIELDS))}",
        )
    rdb = db()
    w = rdb.get_work(ref_id)
    if not w:
        raise HTTPException(404, "参考作品不存在")
    updated = rdb.update_work(
        ref_id, **{body.field: json.dumps(body.data, ensure_ascii=False)},
    )
    if not updated:
        raise HTTPException(500, "更新失败")
    return updated


@router.post("/works/{ref_id}/chronicle/summarize")
async def summarize_chronicle(ref_id: str):
    """Run the chronological-summary AI pass on the currently-persisted
    chronicle. Reads ``plot_outline_json``, flattens all events from all
    epochs/periods, sends them through ``ai_summarize_outline``, and
    returns the reorganized chronicle (does NOT auto-persist — the UI
    decides whether to save).

    On AI failure the response carries ``error`` + the rendered prompt
    + the event list so the UI can switch the user to the manual
    web-LLM path (copy prompt → run in browser → paste back).
    """
    rdb = db()
    w = rdb.get_work(ref_id)
    if not w:
        raise HTTPException(404, "参考作品不存在")

    try:
        plot = json.loads(w.get("plot_outline_json") or "{}")
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
        pipe = FeatureExtractionPipeline(rdb.db_path)
        text = pipe._load_text(w)
        all_chapters = pipe._split_chapters(text) if text else []
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


@router.post("/works/{ref_id}/plot_outline/summarize")
async def summarize_plot_outline(ref_id: str, body: OutlineGranularityRequest):
    """Condense the chapter-level plot outline to a coarser granularity
    (大事件 / 卷 / 全书). Does NOT persist — the UI applies the result.
    With ``prompt_only=true`` returns the prompt for the web-LLM path.
    """
    rdb = db()
    w = rdb.get_work(ref_id)
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
        result = json.loads(strip_json_blob(resp.content or ""))
    except Exception as e:
        return {"ok": False, "error": f"AI 概括失败：{str(e)[:200]}", "prompt": prompt}
    if not isinstance(result, dict) or not result.get("epochs"):
        return {"ok": False, "error": "AI 返回为空，请改用复制 prompt 以使用AI大模型网页版的方式", "prompt": prompt}
    return {"ok": True, "plot_outline": result}


@router.post("/prompts/reference.outline_summary/render")
def render_outline_summary_prompt(body: OutlineSummaryPromptRequest):
    """Render the chronological-summary prompt with the user's accumulated
    events spliced in. Step 2 of the manual outline flow: after running
    all per-chunk extraction prompts and accumulating events, the user
    calls this to get a ready-to-copy summary prompt that asks the LLM
    to reorder by story-time + group into periods/epochs.

    POST (not GET) because the events array can be large and shouldn't
    sit in a URL. The endpoint itself does no AI work — it just renders.
    """
    rdb = db()
    w = rdb.get_work(body.ref_id)
    if not w:
        raise HTTPException(404, "参考作品不存在")
    try:
        from reference_pipeline.pipeline import (
            FeatureExtractionPipeline, build_work_ctx,
        )
        from reference_pipeline.prompts import render
        from reference_pipeline.ai_extractor import _normalize_event

        pipe = FeatureExtractionPipeline(rdb.db_path)
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
