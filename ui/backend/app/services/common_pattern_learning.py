"""多作品共通点学习 (spec 参考数据库功能10 / 参考数据库分析Agent4).

用户选取多个参考作品 + 一个关注维度（整体 / 剧情大纲 / 角色塑造 /
世界观设定 / 叙事节奏 / 语言风格），ONE LLM call 提取它们的共通点并
总结为可执行的写作 skill。Stored DB-native as ``kind='self_learned'``
in skill_index — it survives registry sync and flows into prompts via
the skills loader's embedding recall, alongside every other skill.
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
import uuid
from typing import Any

logger = logging.getLogger("inkoctobot.services.common_pattern_learning")


# 维度 → (label, reference_works columns to feed the prompt)
DIMENSIONS: dict[str, dict] = {
    "overall":    {"label": "整体",     "columns": ["plot_outline_json", "extracted_characters_json", "settings_json", "style_fingerprint_json"]},
    "plot":       {"label": "剧情大纲", "columns": ["plot_outline_json"]},
    "characters": {"label": "角色塑造", "columns": ["extracted_characters_json"]},
    "settings":   {"label": "世界观设定", "columns": ["settings_json", "setting_features_json"]},
    "rhythm":     {"label": "叙事节奏", "columns": ["rhythm_json", "narrative_structure_json"]},
    "style":      {"label": "语言风格", "columns": ["style_fingerprint_json"]},
}

_PER_WORK_CHARS = 3000

_SYSTEM = (
    "你是网文写作技法分析师。用户给出多部参考作品在某个维度上的分析"
    "结果，请提取它们的共通点，总结为一份可直接用于章节写作的 skill。\n"
    "输出 JSON：\n"
    "{\n"
    '  "skill_name": "技法名（8字内）",\n'
    '  "summary": "一句话描述这份共通技法",\n'
    '  "common_patterns": [{"pattern": "共通点", "evidence": "在哪些作品中如何体现", "how_to_apply": "写作时怎么用"}]\n'
    "}\n"
    "要求：\n"
    "1. 只总结确实在多部作品中共同出现的模式，单本独有的不算\n"
    "2. how_to_apply 必须是可执行的写作指令\n"
    "3. 禁止使用 emoji"
)


def _gather_material(
    db_path: str, ref_ids: list[str], dimension: str,
) -> tuple[list[dict], str]:
    """Per-work material for the prompt. Returns (works, prompt_part)."""
    cols = DIMENSIONS[dimension]["columns"]
    works: list[dict] = []
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        from storage.reference_schema import ensure_reference_tables
        ensure_reference_tables(con)
        for rid in ref_ids:
            row = con.execute(
                "SELECT ref_id, title, creator, "
                + ", ".join(f"COALESCE({c}, '') AS {c}" for c in cols)
                + " FROM reference_works WHERE ref_id = ?",
                (rid,),
            ).fetchone()
            if not row:
                continue
            material = "\n".join(
                (row[c] or "")[: _PER_WORK_CHARS // len(cols)]
                for c in cols if (row[c] or "").strip()
            ).strip()
            works.append({
                "ref_id": row["ref_id"],
                "title": row["title"],
                "material": material,
            })
    finally:
        con.close()
    parts: list[str] = []
    for w in works:
        parts.append(f"## 《{w['title']}》")
        parts.append(w["material"] or "（该作品此维度暂无分析数据）")
        parts.append("")
    return works, "\n".join(parts)


def _extract_json(raw: str) -> dict:
    s = raw.strip()
    if s.startswith("```"):
        s = re.sub(r"^```(?:json)?\s*", "", s)
        s = re.sub(r"\s*```$", "", s)
    m = re.search(r"\{.*\}", s, flags=re.DOTALL)
    if not m:
        raise ValueError(f"no JSON in learning response: {raw[:200]}")
    return json.loads(m.group(0))


async def learn_common_patterns(
    project_db_path: str, reference_db_path: str,
    ref_ids: list[str], dimension: str,
) -> dict[str, Any]:
    """ONE LLM call → self_learned skill stored in skill_index."""
    if dimension not in DIMENSIONS:
        raise ValueError(f"dimension must be one of {sorted(DIMENSIONS)}")
    ref_ids = [r for r in dict.fromkeys(ref_ids) if r]
    if len(ref_ids) < 2:
        raise ValueError("至少选择 2 部参考作品")

    works, material = _gather_material(reference_db_path, ref_ids, dimension)
    if len(works) < 2:
        raise ValueError("所选作品不足 2 部存在于参考库")
    if not any(w["material"] for w in works):
        raise ValueError("所选作品在该维度上还没有分析数据，请先完成特征提取")

    dim_label = DIMENSIONS[dimension]["label"]
    from llm.call_site import LLMCallSite
    cs = LLMCallSite(
        call_site_id="reference.common_pattern_learning",
        primary_role="post_commit",
        parsed_target_table="skill_index",
        default_max_tokens=2500, default_temperature=0.3,
    )
    raw = await cs.invoke(
        prompt=(
            f"关注维度：{dim_label}\n"
            f"参考作品（{len(works)} 部）：\n\n{material}\n\n请输出 JSON。"
        ),
        system=_SYSTEM,
        db_path=project_db_path,
    )
    parsed = _extract_json(raw)
    patterns = [
        p for p in (parsed.get("common_patterns") or [])
        if isinstance(p, dict) and str(p.get("pattern") or "").strip()
    ]
    if not patterns:
        raise RuntimeError("未能提取出共通点（LLM 返回为空）")

    name = str(parsed.get("skill_name") or f"{dim_label}共通技法").strip()[:24]
    summary = str(parsed.get("summary") or "").strip()
    titles = "、".join(f"《{w['title']}》" for w in works)
    body_lines = [
        f"来源：从 {titles} 的{dim_label}维度学习（自学习 skill）",
        summary,
        "",
    ]
    for i, p in enumerate(patterns, 1):
        body_lines.append(f"{i}. {str(p['pattern']).strip()}")
        ev = str(p.get("evidence") or "").strip()
        if ev:
            body_lines.append(f"   体现：{ev}")
        how = str(p.get("how_to_apply") or "").strip()
        if how:
            body_lines.append(f"   应用：{how}")
    body = "\n".join(body_lines)

    from ui.backend.app.services import skill_index
    skill_id = f"self_learned_{uuid.uuid4().hex[:10]}"
    stored = skill_index.upsert_skill(project_db_path, {
        "skill_id":     skill_id,
        "display_name": f"自学习：{name}",
        "description":  f"{dim_label}维度共通技法（来自 {len(works)} 部参考作品）",
        "kind":         "self_learned",
        "body_snippet": body,
    })
    return {
        **stored,
        "dimension": dimension,
        "works": [{"ref_id": w["ref_id"], "title": w["title"]} for w in works],
        "patterns_count": len(patterns),
    }
