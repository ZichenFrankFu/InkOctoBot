"""On-stage character cards loader (LOADER_SPEC Loader 5).

Renders the user-selected character cards with snapshot-aware overrides.
For each character we ask ``CharacterSnapshotResolver`` what state the
character is in at the current chapter, and dispatch to one of four
renderers:

- ``stable``                  → baseline (possibly a completed snapshot
                                or just the base card)
- ``transition_event``        → "this chapter is a beat of the
                                transition" — describes the from/to
                                arc and asks the Writer to add a beat
- ``transition_gap``          → "mid-transition but not a beat" — show
                                wavering / interim behavior
- ``transition_complete``     → "the transition finishes here" — render
                                the new state and call it out

The legacy ``dynamic_snapshots`` extras field is gone; snapshot data
lives in ``character_snapshots`` (Batch 5).
"""
from __future__ import annotations

import logging
from typing import Any

from ..loader_protocol import LoaderPlan, make_plan
from ..utils import clip, section

logger = logging.getLogger("inkoctobot.services.prompt_context.character_cards")


_LAYER_B_LABELS = {
    "loss_aversion":       "损失厌恶",
    "risk_aversion_gain":  "收益侧风险厌恶",
    "risk_aversion_loss":  "损失侧风险厌恶",
    "impulse_probability": "冲动概率",
    "social_frequency":    "社交频率",
    "time_discount":       "时间贴现",
    "decision_threshold":  "决策阈值",
}

_VALUE_WEIGHT_LABELS = {
    "survival": "生存", "power": "权力", "love": "情感",
    "loyalty":  "忠诚", "justice": "正义", "freedom": "自由",
}


def _format_layer_b(lb: dict) -> str:
    if not isinstance(lb, dict) or not lb:
        return ""
    bits: list[str] = []
    for key, label in _LAYER_B_LABELS.items():
        if key in lb and lb[key] is not None:
            try:
                bits.append(f"{label} {float(lb[key]):.2g}")
            except (TypeError, ValueError):
                continue
    vw = lb.get("value_weights")
    if isinstance(vw, dict) and vw:
        weights: list[str] = []
        for vk, vv in vw.items():
            try:
                label = _VALUE_WEIGHT_LABELS.get(vk, vk)
                weights.append(f"{label} {float(vv):.0%}")
            except (TypeError, ValueError):
                continue
        if weights:
            bits.append("价值权重：" + "、".join(weights))
    return "；".join(bits)


def _format_relations(relations_overrides: dict) -> list[str]:
    """Render snapshot.relations_overrides as bullet lines."""
    if not isinstance(relations_overrides, dict) or not relations_overrides:
        return []
    out: list[str] = []
    hidden = relations_overrides.get("__hidden_identities__")
    for target, info in relations_overrides.items():
        if target == "__hidden_identities__":
            continue
        if not isinstance(info, dict):
            continue
        label = (info.get("label") or info.get("relation_type") or "").strip()
        sentiment = info.get("sentiment_score") or info.get("affinity")
        trust = info.get("trust_level") or info.get("trust")
        notes = (info.get("notes") or "").strip()
        line = f"  对{target}"
        if label:
            line += f"（{label}）"
        bits: list[str] = []
        if sentiment is not None:
            bits.append(f"sentiment {sentiment}")
        if trust is not None:
            bits.append(f"trust {trust}")
        if bits:
            line += "：" + "，".join(bits)
        if notes:
            line += f"；{notes}"
        out.append(line)
    if isinstance(hidden, list) and hidden:
        out.append("  隐藏身份 / 化名：")
        for h in hidden:
            if not isinstance(h, dict):
                continue
            name = (h.get("name") or "").strip()
            if not name:
                continue
            revealed = [
                str(r).strip() for r in (h.get("revealed_to") or [])
                if str(r or "").strip()
            ]
            notes = (h.get("notes") or "").strip()
            sub = f"    - 化名「{name}」"
            sub += f"（已知真相：{'、'.join(revealed)}）" if revealed else "（无人知晓真实身份）"
            if notes:
                sub += f"；{notes}"
            out.append(sub)
    return out


def _base_card(character: dict) -> dict:
    """Convert a character row into a snapshot-shaped baseline."""
    return {
        "personality_override":  character.get("personality") or "",
        "speech_style_override": character.get("speech_style") or "",
        "alias":                 "",
        "layer_b_overrides":     character.get("layer_b") or {},
        "relations_overrides":   {},
        "other_changes":         character.get("background") or "",
    }


def _effective(
    character: dict, snapshot: dict | None,
) -> dict[str, Any]:
    """Merge baseline character fields with a snapshot's overrides."""
    base = _base_card(character)
    if snapshot is None:
        return {
            "personality":  base["personality_override"],
            "speech_style": base["speech_style_override"],
            "alias":        "",
            "layer_b":      base["layer_b_overrides"],
            "relations":    {},
            "background":   character.get("background") or "",
        }
    return {
        "personality":  (snapshot.get("personality_override") or "").strip() or base["personality_override"],
        "speech_style": (snapshot.get("speech_style_override") or "").strip() or base["speech_style_override"],
        "alias":        (snapshot.get("alias") or "").strip(),
        "layer_b":      snapshot.get("layer_b_overrides") or base["layer_b_overrides"],
        "relations":    snapshot.get("relations_overrides") or {},
        "background":   (snapshot.get("other_changes") or "").strip() or character.get("background") or "",
    }


# ─────────── per-state renderers ───────────


def _render_header(character: dict, alias: str = "") -> list[str]:
    name = character.get("name") or ""
    role = (character.get("role") or "").strip()
    head = f"【{name}】" + (f"（{role}）" if role else "")
    if alias:
        head += f"（化名：{alias}）"
    lines = [head]
    appearance = (character.get("appearance") or "").strip()
    if appearance:
        lines.append(f"  外貌：{appearance}")
    base_bg = (character.get("background") or "").strip()
    if base_bg:
        lines.append(f"  背景：{base_bg}")
    return lines


def _render_body(character: dict, eff: dict) -> list[str]:
    lines: list[str] = []
    if eff["personality"]:
        lines.append(f"  性格：{eff['personality']}")
    if eff["speech_style"]:
        lines.append(f"  说话方式：{eff['speech_style']}")
    lb_text = _format_layer_b(eff["layer_b"] or {})
    if lb_text:
        lines.append(f"  决策倾向（Layer B）：{lb_text}")
    rel_lines = _format_relations(eff["relations"] or {})
    if rel_lines:
        lines.append("  关系：")
        lines.extend(rel_lines)
    return lines


def _render_stable(character: dict, baseline: dict | None) -> str:
    eff = _effective(character, baseline)
    lines = _render_header(character, eff["alias"])
    if baseline is not None:
        lines.append(
            f"  [当前状态]（自第 {baseline.get('transition_complete_chapter')} 章起完成 "
            f"Snapshot {baseline.get('snapshot_order')}）"
        )
    lines.extend(_render_body(character, eff))
    return "\n".join(lines)


# 角色卡·机制2: 转变态 3 档位的提示词指导。
_STAGE_GUIDANCE = {
    "wavering": "动摇 — 对原有状态产生怀疑，但行为仍以旧状态为主，偶有迟疑",
    "probing":  "试探 — 开始尝试新状态的行为方式，谨慎试水，结果不确定",
    "leaning":  "倾向 — 不得不承认新状态更有用，行为多数已偏向新状态，"
                "但尚未完全切换、关键时刻仍可能退回",
}


def _append_stage_line(lines: list[str], resolution: dict) -> None:
    stage = resolution.get("transition_stage")
    guidance = _STAGE_GUIDANCE.get(stage or "")
    if not guidance:
        return
    src = "用户设定" if resolution.get("transition_stage_source") == "user" \
        else "按进度推导"
    lines.append(f"  转变档位（{src}）：{guidance}")


def _render_transition_event(
    character: dict, resolution: dict, chapter_num: int,
) -> str:
    in_trans = resolution["in_transition"]
    previous = resolution["previous_snapshot"]
    baseline = resolution["baseline_snapshot"]
    eff = _effective(character, in_trans)
    lines = _render_header(character, eff["alias"])
    prev_label = (
        f"Snapshot {previous.get('snapshot_order')}" if previous
        else "基线人设"
    )
    target_label = f"Snapshot {in_trans.get('snapshot_order')}"
    lines.append(f"  [正在转变]（从 {prev_label} 到 {target_label}）")
    if (in_trans.get("trigger_description") or "").strip():
        lines.append(f"  触发：{in_trans['trigger_description'].strip()}")
    _append_stage_line(lines, resolution)
    lines.append("  本章是关键转变事件之一")
    bound = in_trans.get("bound_chapters") or []
    if bound:
        bound_text = ", ".join(str(c) for c in bound)
        lines.append(f"  绑定章节：{bound_text}（本章: {chapter_num}）")
    comp = in_trans.get("transition_complete_chapter")
    if comp is not None:
        gap = int(comp) - int(chapter_num)
        lines.append(
            f"  完成章节：第 {comp} 章" +
            (f"（距完成还有 {gap} 章）" if gap > 0 else "")
        )
    # Previous-state hint so the Writer knows what to migrate from.
    if previous is not None:
        prev_eff = _effective(character, previous)
        if prev_eff["personality"] and eff["personality"]:
            lines.append(
                f"  当前应表现：从「{prev_eff['personality']}」"
                f"逐渐向「{eff['personality']}」过渡"
            )
        elif eff["personality"]:
            lines.append(f"  当前应表现：开始展现「{eff['personality']}」")
    lines.append("  本章可写：第一次出现的相关情绪/行为/认知变化")
    lines.extend(_render_body(character, eff))
    return "\n".join(lines)


def _render_transition_gap(
    character: dict, resolution: dict, chapter_num: int,
) -> str:
    in_trans = resolution["in_transition"]
    previous = resolution["previous_snapshot"]
    eff = _effective(character, in_trans)
    lines = _render_header(character, eff["alias"])
    prev_label = (
        f"Snapshot {previous.get('snapshot_order')}" if previous
        else "基线人设"
    )
    target_label = f"Snapshot {in_trans.get('snapshot_order')}"
    lines.append("  [transition 间歇期]")
    lines.append(f"  正在从 {prev_label} 向 {target_label} 过渡")
    _append_stage_line(lines, resolution)
    bound = in_trans.get("bound_chapters") or []
    if bound:
        lines.append(
            f"  绑定章节：{', '.join(str(c) for c in bound)}"
            f"（本章 {chapter_num} 不在其中）"
        )
    lines.append("  角色处于不稳定中间状态")
    lines.append("  本章可表现：转变中的反复、波动、暂时回旧状态")
    lines.append("  无需强行推进转变")
    lines.extend(_render_body(character, eff))
    return "\n".join(lines)


def _render_transition_complete(
    character: dict, resolution: dict, chapter_num: int,
) -> str:
    in_trans = resolution["in_transition"]
    previous = resolution["previous_snapshot"]
    eff = _effective(character, in_trans)
    lines = _render_header(character, eff["alias"])
    prev_label = (
        f"Snapshot {previous.get('snapshot_order')}" if previous
        else "基线人设"
    )
    target_label = f"Snapshot {in_trans.get('snapshot_order')}"
    lines.append(f"  [转变完成]（本章 {prev_label} 到 {target_label} 完成）")
    if (in_trans.get("trigger_description") or "").strip():
        lines.append(f"  触发：{in_trans['trigger_description'].strip()}")
    bound = in_trans.get("bound_chapters") or []
    if bound:
        lines.append(f"  转变历程：第 {', '.join(str(c) for c in bound)} 章")
    lines.append("  本章应表现：转变定型，角色明确进入新状态")
    lines.append("")
    lines.append("  转变后的状态：")
    lines.extend(_render_body(character, eff))
    return "\n".join(lines)


# ─────────── main entry ───────────


_BLOCK = "character_cards"
_TITLE = "出场角色档案"


def _build_body(
    project_id: str, names: list[str], exclude: set | None, chapter_num: int,
) -> str:
    if not names:
        return ""
    try:
        from ui.backend.app.services import project_store
        from ui.backend.app.services import character_snapshot_resolver as _resolver
        from ui.backend.app.services.project_paths import get_db_path

        db_path = get_db_path()
        rows = project_store.list_characters(db_path, project_id)
        by_name = {r.get("name", ""): r for r in rows}
        cards: list[str] = []
        for name in names:
            if exclude and name in exclude:
                continue
            c = by_name.get(name)
            if not c:
                continue
            try:
                resolution = _resolver.resolve(db_path, c["id"], chapter_num)
            except Exception as e:
                logger.debug("snapshot resolve skipped for %s: %s", name, e)
                resolution = {
                    "baseline_snapshot": None,
                    "in_transition": None,
                    "transition_status": "stable",
                    "previous_snapshot": None,
                }
            status = resolution["transition_status"]
            if status == "transition_event":
                card = _render_transition_event(c, resolution, chapter_num)
            elif status == "transition_gap":
                card = _render_transition_gap(c, resolution, chapter_num)
            elif status == "transition_complete":
                card = _render_transition_complete(c, resolution, chapter_num)
            else:
                card = _render_stable(c, resolution["baseline_snapshot"])
            cards.append(card)
        return "\n\n".join(cards)
    except Exception as e:
        logger.debug("character cards skipped: %s", e)
        return ""


def plan(
    project_id: str,
    names: list[str],
    exclude: set | None = None,
    *,
    chapter_num: int = 0,
) -> LoaderPlan | None:
    return make_plan(_BLOCK, _TITLE,
                       _build_body(project_id, names, exclude, chapter_num))


def load(
    project_id: str,
    names: list[str],
    exclude: set | None = None,
    *,
    chapter_num: int = 0,
) -> str:
    p = plan(project_id, names, exclude, chapter_num=chapter_num)
    return p.render(p.target) if p else ""
