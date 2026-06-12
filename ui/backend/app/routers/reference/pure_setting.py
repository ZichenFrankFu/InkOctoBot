"""纯设定作品 (spec 2.2.2 / 6.2) — SCP、后室、战锤40K 等无完整正文的
众创/设定集作品。

Surfaces:
- GET/PUT /works/{ref_id}/pure-setting — structure type, 快捷输入原文,
  设定条目 (6 类 + 其他), 静态角色 (姓名/定位/描述, 不绑定章节),
  设定特征 (作品级世界观高概念/母题)
- POST /works/{ref_id}/pure-setting/extract — ONE LLM call over the
  quick-input wiki text, returning a PREVIEW {settings, characters,
  setting_features}; nothing persists until the client PUTs the
  user-pruned lists back (预览后逐项入库, LLM交互·机制2)
"""
from __future__ import annotations

import json
import re
import sqlite3
from typing import Any

from fastapi import APIRouter, Body, HTTPException

from ._common import reference_db_path

router = APIRouter(tags=["reference-pure-setting"])

STRUCTURE_TYPES = ("narrative", "setting_collection")

# 复用叙事型的 6 类设定分类 + 其他容纳异常/怪物 (spec 6.2)。
SETTING_CATEGORIES = (
    "力量体系", "势力组织", "地理", "社会规则", "历史背景", "世界观", "其他",
)

_SYSTEM = (
    "你是设定集分析助手。用户粘贴了某个共创世界观作品（如 SCP、后室、"
    "战锤40K）的 wiki 条目原文，请从中抽取结构化设定。\n"
    "输出 JSON：\n"
    "{\n"
    '  "settings": [{"category": "力量体系|势力组织|地理|社会规则|历史背景|世界观|其他", "title": "条目名", "content": "条目内容概述"}],\n'
    '  "characters": [{"name": "姓名", "role": "定位", "description": "描述"}],\n'
    '  "setting_features": [{"title": "高概念/母题", "description": "一句话解释"}]\n'
    "}\n"
    "要求：\n"
    "1. settings 按原文忠实概括，不要自行虚构\n"
    "2. characters 为静态条目（不绑定章节），只收录有名字的个体\n"
    "3. setting_features 是作品级世界观高概念/核心母题"
    "（如战锤40K的「太空大航海」「唯心影响现实世界」），1-6 条\n"
    "4. 禁止使用 emoji"
)


def _conn() -> sqlite3.Connection:
    con = sqlite3.connect(reference_db_path())
    con.row_factory = sqlite3.Row
    from storage.reference_schema import ensure_reference_tables
    ensure_reference_tables(con)
    return con


def _loads(raw: Any) -> list:
    try:
        v = json.loads(raw or "[]")
        return v if isinstance(v, list) else []
    except Exception:
        return []


@router.get("/works/{ref_id}/pure-setting")
def get_pure_setting(ref_id: str):
    with _conn() as con:
        row = con.execute(
            "SELECT structure_type, quick_input_text, settings_json, "
            "static_characters_json, setting_features_json "
            "FROM reference_works WHERE ref_id = ?",
            (ref_id,),
        ).fetchone()
    if not row:
        raise HTTPException(404, "work not found")
    return {
        "ref_id": ref_id,
        "structure_type": row["structure_type"] or "narrative",
        "quick_input_text": row["quick_input_text"] or "",
        "settings": _loads(row["settings_json"]),
        "static_characters": _loads(row["static_characters_json"]),
        "setting_features": _loads(row["setting_features_json"]),
    }


@router.put("/works/{ref_id}/pure-setting")
def update_pure_setting(ref_id: str, body: dict = Body(...)):
    """Wholesale update of any pure-setting field — manual 增删改 and
    the 逐项入库 act both land here with the client's full lists."""
    sets: list[str] = []
    args: list[Any] = []
    if "structure_type" in body:
        st = body.get("structure_type")
        if st not in STRUCTURE_TYPES:
            raise HTTPException(400, f"structure_type must be one of {STRUCTURE_TYPES}")
        sets.append("structure_type = ?")
        args.append(st)
    if "quick_input_text" in body:
        sets.append("quick_input_text = ?")
        args.append(str(body.get("quick_input_text") or ""))
    for key, col in (
        ("settings", "settings_json"),
        ("static_characters", "static_characters_json"),
        ("setting_features", "setting_features_json"),
    ):
        if key in body:
            v = body.get(key)
            if not isinstance(v, list):
                raise HTTPException(400, f"{key} must be a list")
            sets.append(f"{col} = ?")
            args.append(json.dumps(v, ensure_ascii=False))
    if not sets:
        raise HTTPException(400, "nothing to update")
    sets.append("updated_at = CURRENT_TIMESTAMP")
    with _conn() as con:
        cur = con.execute(
            f"UPDATE reference_works SET {', '.join(sets)} WHERE ref_id = ?",
            (*args, ref_id),
        )
        con.commit()
    if cur.rowcount == 0:
        raise HTTPException(404, "work not found")
    return get_pure_setting(ref_id)


def _extract_json(raw: str) -> dict:
    s = raw.strip()
    if s.startswith("```"):
        s = re.sub(r"^```(?:json)?\s*", "", s)
        s = re.sub(r"\s*```$", "", s)
    m = re.search(r"\{.*\}", s, flags=re.DOTALL)
    if not m:
        raise ValueError(f"no JSON in extraction response: {raw[:200]}")
    return json.loads(m.group(0))


def _normalize_preview(parsed: dict) -> dict:
    settings: list[dict] = []
    for s in (parsed.get("settings") or []):
        if not isinstance(s, dict):
            continue
        title = str(s.get("title") or "").strip()
        content = str(s.get("content") or "").strip()
        if not title and not content:
            continue
        cat = str(s.get("category") or "其他").strip()
        if cat not in SETTING_CATEGORIES:
            cat = "其他"
        settings.append({"category": cat, "title": title, "content": content})
    characters: list[dict] = []
    for c in (parsed.get("characters") or []):
        if not isinstance(c, dict):
            continue
        name = str(c.get("name") or "").strip()
        if not name:
            continue
        characters.append({
            "name": name,
            "role": str(c.get("role") or "").strip(),
            "description": str(c.get("description") or "").strip(),
        })
    features: list[dict] = []
    for f in (parsed.get("setting_features") or []):
        if not isinstance(f, dict):
            continue
        title = str(f.get("title") or "").strip()
        if not title:
            continue
        features.append({
            "title": title,
            "description": str(f.get("description") or "").strip(),
        })
    return {
        "settings": settings,
        "characters": characters,
        "setting_features": features,
    }


@router.post("/works/{ref_id}/pure-setting/extract")
async def extract_pure_setting(ref_id: str, body: dict = Body(default={})):
    """ONE LLM call over 快捷输入 → preview lists (NOT persisted)."""
    text = str(body.get("text") or "").strip()
    if not text:
        with _conn() as con:
            row = con.execute(
                "SELECT quick_input_text FROM reference_works WHERE ref_id = ?",
                (ref_id,),
            ).fetchone()
        if not row:
            raise HTTPException(404, "work not found")
        text = (row["quick_input_text"] or "").strip()
    if not text:
        raise HTTPException(400, "快捷输入为空 — 请先粘贴 wiki 条目原文")

    from llm.call_site import LLMCallSite
    cs = LLMCallSite(
        call_site_id="reference.pure_setting_extract",
        primary_role="post_commit",
        parsed_target_table="reference_works",
        default_max_tokens=3000, default_temperature=0.2,
    )
    raw = await cs.invoke(
        prompt=f"wiki 条目原文：\n\n{text[:12000]}\n\n请输出 JSON。",
        system=_SYSTEM,
        project_id=ref_id,
    )
    try:
        preview = _normalize_preview(_extract_json(raw))
    except Exception as e:
        raise HTTPException(500, f"提取结果解析失败: {e}")
    return {"ref_id": ref_id, "preview": True, **preview}
