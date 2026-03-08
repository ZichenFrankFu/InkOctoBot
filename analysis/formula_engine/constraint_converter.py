"""
Convert aggregated features into prompt constraints.

Produces constraint rules compatible with
``agents.constraints.assembler.ConstraintAssembler``.

Priority levels (matching assembler.py):
    1 – 世界观硬规则
    2 – 知识隔离指令
    3 – 情节约束
    4 – 风格约束
"""
from __future__ import annotations

from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from .presets import GenrePreset


def convert_to_constraints(
    aggregated: dict[str, Any],
    preset: GenrePreset | None = None,
) -> list[dict[str, Any]]:
    """Convert aggregated feature scores into constraint rules.

    Parameters
    ----------
    aggregated : dict
        Output of ``aggregator.aggregate_features()``.
    preset : GenrePreset, optional
        If provided, its ``constraint_templates`` are included as base rules.

    Returns
    -------
    list[dict]
        Each dict has keys: ``level``, ``category``, ``text``,
        ``good_example`` (optional), ``bad_example`` (optional).
    """
    rules: list[dict[str, Any]] = []

    # 1. Include preset templates
    if preset:
        for tmpl in preset.constraint_templates:
            rules.append({
                "level": tmpl.get("priority", 4),
                "category": tmpl.get("rule_type", "style_constraint"),
                "text": tmpl.get("positive_restatement") or tmpl.get("description", ""),
                "good_example": tmpl.get("good_example", ""),
                "bad_example": tmpl.get("bad_example", ""),
            })

    dims = aggregated.get("dimension_scores", {})

    # 2. Dialogue ratio guidance (style constraint)
    dlg = dims.get("dialogue_ratio")
    if dlg is not None:
        if dlg < 30:
            rules.append({
                "level": 4,
                "category": "style_constraint",
                "text": "增加对话比例，当前文本叙述过多，建议对话占比提升至30%以上",
                "good_example": "通过对话推动情节和展示人物性格",
                "bad_example": "大段纯叙述描写没有任何对话",
            })
        elif dlg > 80:
            rules.append({
                "level": 4,
                "category": "style_constraint",
                "text": "适当减少对话，增加叙述、环境描写和心理描写",
                "good_example": "对话与叙述交替推进，节奏均衡",
                "bad_example": "连续多段纯对话无任何叙述穿插",
            })

    # 3. Pacing guidance (plot constraint)
    pacing = dims.get("pacing")
    if pacing is not None and pacing < 30:
        target = ""
        if preset and preset.pacing_profile:
            parts = [f"{k}{int(v*100)}%" for k, v in preset.pacing_profile.items()]
            target = f"（目标分布: {', '.join(parts)}）"
        rules.append({
            "level": 3,
            "category": "plot_constraint",
            "text": f"调整叙事节奏，当前节奏与目标差距较大{target}",
        })

    # 4. Rhetoric density (style constraint)
    rhet = dims.get("rhetoric_frequency")
    if rhet is not None and rhet > 85:
        rules.append({
            "level": 4,
            "category": "style_constraint",
            "text": "降低修辞密度，避免过度使用比喻、排比等修辞手法",
            "good_example": "关键场景使用精炼修辞增强表现力",
            "bad_example": "每句话都使用修辞手法，读起来华而不实",
        })

    # 5. Shuangdian density (plot constraint)
    sd = dims.get("shuangdian")
    if sd is not None and sd < 25:
        types_hint = ""
        if preset and preset.shuangdian_types:
            types_hint = f"（推荐类型: {', '.join(preset.shuangdian_types[:3])}）"
        rules.append({
            "level": 3,
            "category": "plot_constraint",
            "text": f"增加爽点场景密度，确保读者持续获得满足感{types_hint}",
        })

    # 6. Recommendations as advisory constraints
    for rec in aggregated.get("recommendations", []):
        # Avoid duplicating rules already added above
        if not any(rec in r.get("text", "") for r in rules):
            rules.append({
                "level": 4,
                "category": "style_constraint",
                "text": rec,
            })

    return rules
