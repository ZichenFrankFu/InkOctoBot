"""Reference-work content condensers.

Each ``condense_*`` takes a raw JSON value (or already-parsed dict/list)
from a row in the reference DB and produces a single readable paragraph
for prompt injection. They never raise; malformed input → ``""``.

Also hosts ``build_referenced_materials_block``, the chapter-tab variant
that mixes selected events + inspirations with enriched reference data.
"""
from __future__ import annotations

import logging
from typing import Any

from .budgets import BUDGETS
from .utils import clip, coerce_json, section

logger = logging.getLogger("inkoctobot.services.prompt_context.references")


def condense_ref_characters(raw: Any) -> str:
    data = coerce_json(raw)
    chars = data.get("characters") if isinstance(data, dict) else data
    if not isinstance(chars, list):
        return ""
    out: list[str] = []
    for c in chars[:5]:
        if not isinstance(c, dict):
            continue
        name = (c.get("name") or "").strip()
        if not name:
            continue
        intro = (c.get("intro") or "").strip()
        tag = (c.get("role_tag") or "").strip()
        line = f"- {name}" + (f"（{tag}）" if tag else "")
        if intro:
            line += f"：{intro}"
        out.append(line)
    return "角色原型：\n" + "\n".join(out) if out else ""


def condense_ref_settings(raw: Any) -> str:
    data = coerce_json(raw)
    items = data.get("settings") if isinstance(data, dict) else data
    if not isinstance(items, list):
        return ""
    out: list[str] = []
    for s in items[:6]:
        if not isinstance(s, dict):
            continue
        title = (s.get("title") or "").strip()
        summary = (s.get("summary") or s.get("content") or "").strip()
        if not title and not summary:
            continue
        out.append(f"- {title}：{summary}".strip("：").strip())
    return "世界设定：\n" + "\n".join(out) if out else ""


def condense_ref_plot(raw: Any) -> str:
    data = coerce_json(raw)
    if not isinstance(data, dict):
        return ""
    parts: list[str] = []
    logline = (data.get("logline") or "").strip()
    if logline:
        parts.append(f"主线：{logline}")
    epochs = data.get("epochs")
    if isinstance(epochs, list) and epochs:
        titles = [
            (e.get("title") or "").strip()
            for e in epochs[:8]
            if isinstance(e, dict) and (e.get("title") or "").strip()
        ]
        if titles:
            parts.append("阶段：" + " → ".join(titles))
    return "剧情结构：\n" + "\n".join(parts) if parts else ""


def condense_ref_rhythm(raw: Any, style_fp: Any) -> str:
    data = coerce_json(raw)
    parts: list[str] = []
    if isinstance(data, dict):
        op = (data.get("opening_pattern") or "").strip()
        if op:
            parts.append(f"开篇方式：{op}")
        segs = data.get("pacing_segments")
        if isinstance(segs, list) and segs:
            kinds = [s.get("pacing", "") for s in segs if isinstance(s, dict)]
            kinds = [k for k in kinds if k]
            if kinds:
                parts.append("节奏分布：" + "、".join(kinds))
        ph: list[str] = []
        for key, label in (("payoff_density", "爽点密度"), ("hook_density", "钩子密度")):
            v = data.get(key)
            if v is not None:
                try:
                    ph.append(f"{label} {float(v):.0%}")
                except Exception:
                    pass
        if ph:
            parts.append("爽点 / 钩子：" + "、".join(ph))
    fp = coerce_json(style_fp)
    if isinstance(fp, dict):
        bits: list[str] = []
        if fp.get("dialogue_ratio") is not None:
            try:
                bits.append(f"对话比 {float(fp['dialogue_ratio']):.0%}")
            except Exception:
                pass
        if fp.get("description_density") is not None:
            try:
                bits.append(f"描写密度 {float(fp['description_density']):.0%}")
            except Exception:
                pass
        if bits:
            parts.append("文风指纹：" + "、".join(bits))
    return "叙事节奏：\n" + "\n".join(parts) if parts else ""


def build_referenced_materials_block(
    events: list[dict] | None,
    inspirations: list[dict] | None,
    db_path: str = "",
) -> str:
    """Build the chapter's reference block from its 大纲-tab linked events
    and inspirations. Each referenced work is enriched from the reference
    database with its full-book plot outline, character roster and
    rhythm / payoff / hook profile."""
    by_work: dict[str, list[dict]] = {}
    for e in (events or []):
        rid = str(e.get("ref_id") or e.get("work_title") or e.get("ref_title") or "")
        by_work.setdefault(rid, []).append(e)
    rdb = None
    if db_path and by_work:
        try:
            from knowledge.reference_db import ReferenceDB
            rdb = ReferenceDB(db_path)
        except Exception:
            rdb = None
    blocks: list[str] = []
    for rid, evs in by_work.items():
        wt = str(evs[0].get("work_title") or evs[0].get("ref_title") or rid).strip()
        seg = [f"《{wt}》"]
        work = None
        if rdb is not None:
            try:
                work = rdb.get_work(rid)
            except Exception:
                work = None
        if work:
            for cond in (
                condense_ref_plot(work.get("plot_outline_json")),
                condense_ref_characters(work.get("extracted_characters_json")),
                condense_ref_rhythm(work.get("rhythm_json"), work.get("style_fingerprint_json")),
            ):
                if cond and cond.strip():
                    seg.append(cond.strip())
        ev_lines: list[str] = []
        for e in evs:
            nm = str(e.get("name") or "").strip()
            desc = str(e.get("description") or "").strip()
            ch = str(e.get("chapter") or "").strip()
            head = nm + (f"（{ch}）" if ch else "")
            if not head and not desc:
                continue
            ev_lines.append(f"- {head}：{desc}" if desc else f"- {head}")
        if ev_lines:
            seg.append("关联事件（user 选取）：\n" + "\n".join(ev_lines))
        if len(seg) > 1:
            blocks.append("\n".join(seg))
    insp_lines: list[str] = []
    for ins in (inspirations or []):
        t = str(ins.get("title") or "").strip()
        c = str(ins.get("content") or "").strip()
        if not t and not c:
            continue
        insp_lines.append(f"- {t}：{c}" if t else f"- {c}")
    if insp_lines:
        blocks.append("关联灵感：\n" + "\n".join(insp_lines))
    if not blocks:
        return ""
    body = clip("\n\n".join(blocks), BUDGETS["referenced_materials"])
    return section(
        "关联参考作品与灵感（参考作品含全书剧情大纲 / 角色 / 节奏·爽点·钩子）", body,
    )
