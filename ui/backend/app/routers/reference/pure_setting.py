"""纯设定作品 (spec 2.2.2 / 6.2) — SCP、后室、战锤40K 等无完整正文的
众创/设定集作品。

Surfaces:
- GET/PUT /works/{ref_id}/pure-setting — structure type, 快捷输入原文,
  设定条目 (6 类 + 其他), 静态角色 (姓名/定位/描述, 不绑定章节),
  设定特征 (作品级世界观高概念/母题)
- GET /works/{ref_id}/pure-setting/segments — chunk plan for the
  quick-input wiki text (满足 LLM 交互机制 4：长文本分段，每段独立 prompt)
- POST /works/{ref_id}/pure-setting/extract — extract one chunk (or
  the whole text when short) via the configured LLM API, returning a
  PREVIEW {settings, characters, setting_features}; nothing persists
  until the client PUTs the user-pruned lists back (预览后逐项入库,
  LLM交互·机制2)

满足 LLM 交互机制 1：本路由提供 API 提取模式；网页版模式通过
``/prompts/reference.pure_setting/preview`` 渲染 prompt 由前端发布给
用户复制，结果直接在前端粘贴解析。
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

# 单段最大字符数 — 与 ai_extractor._MAX_PROMPT_CHARS 保持一致，避免分段提取
# 时 prompt 太长触发上下文限制；同时也是网页版复制 prompt 的安全上限。
_MAX_CHUNK_CHARS = 12_000


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


def _split_chunks(text: str, max_chars: int = _MAX_CHUNK_CHARS) -> list[dict]:
    """Split the quick-input wiki text into chunks of <= max_chars.

    Greedy split that respects段落/章节边界: first tries双换行（段落），
    then单换行，falls back to硬切. Returns a list of
    {chunk_index, total_chunks, text, n_chars} dicts.
    """
    if not text:
        return []
    text = text.strip()
    if len(text) <= max_chars:
        return [{
            "chunk_index": 0, "total_chunks": 1,
            "text": text, "n_chars": len(text),
        }]

    # Prefer段落边界 — split on blank lines first.
    paragraphs = re.split(r"\n\s*\n", text)
    if len(paragraphs) == 1:
        # Fall back to线性切分 by newline
        paragraphs = text.split("\n")

    chunks: list[str] = []
    cur = ""
    for p in paragraphs:
        p = p.strip()
        if not p:
            continue
        candidate = f"{cur}\n\n{p}" if cur else p
        if len(candidate) > max_chars and cur:
            chunks.append(cur)
            cur = p
        else:
            cur = candidate
    if cur:
        chunks.append(cur)

    # Any single段落 itself >  max_chars: hard-cut it.
    final: list[str] = []
    for c in chunks:
        if len(c) <= max_chars:
            final.append(c)
            continue
        # hard-cut on punctuation if possible
        i = 0
        while i < len(c):
            end = min(i + max_chars, len(c))
            # try to break at句号 within last 500 chars
            if end < len(c):
                window = c[end - 500:end]
                m = max((window.rfind(p) for p in "。！？\n"), default=-1)
                if m >= 0:
                    end = end - 500 + m + 1
            final.append(c[i:end].strip())
            i = end

    total = len(final)
    return [{
        "chunk_index": i, "total_chunks": total,
        "text": t, "n_chars": len(t),
    } for i, t in enumerate(final)]


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


@router.get("/works/{ref_id}/pure-setting/segments")
def get_pure_setting_segments(ref_id: str):
    """Return the chunk plan for this work's 快捷输入文本.

    Long wiki dumps (战锤40K 一个种族就上万字) need to be split into
    multiple LLM calls. Returns a list of chunk metadata
    ``{chunk_index, total_chunks, n_chars, preview}`` so the UI can
    render a per-chunk extraction row without itself running the split.
    """
    with _conn() as con:
        row = con.execute(
            "SELECT title, creator, quick_input_text "
            "FROM reference_works WHERE ref_id = ?",
            (ref_id,),
        ).fetchone()
    if not row:
        raise HTTPException(404, "work not found")
    text = (row["quick_input_text"] or "").strip()
    chunks = _split_chunks(text)
    return {
        "ref_id": ref_id,
        "title": row["title"] or "",
        "creator": row["creator"] or "",
        "total_chars": len(text),
        "total_chunks": len(chunks),
        "max_chunk_chars": _MAX_CHUNK_CHARS,
        "chunks": [
            {
                "chunk_index": c["chunk_index"],
                "total_chunks": c["total_chunks"],
                "n_chars": c["n_chars"],
                # 仅返回前 80 字作为列表显示用，正文不重复 traffic
                "preview": c["text"][:80],
            }
            for c in chunks
        ],
    }


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
        content = str(s.get("content") or s.get("summary") or "").strip()
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


def _render_pure_setting_prompt(
    *, title: str, author: str,
    chunk_index: int, total_chunks: int,
    text: str,
) -> tuple[str, str]:
    """Render the user prompt + return the (empty) system prompt.

    The pure-setting prompt template carries every constraint inline so
    the system prompt stays empty — same shape as the other reference
    prompts (`reference.unified`, etc.) which keep `system=""` and put
    the schema inside the user prompt.
    """
    from reference_pipeline.prompts import render
    user_prompt = render(
        "reference.pure_setting",
        title=title or "",
        author=author or "",
        chunk_index_human=chunk_index + 1,
        total_chunks=total_chunks,
        n_chars=len(text),
        text=text,
    )
    return user_prompt, ""


@router.post("/works/{ref_id}/pure-setting/extract")
async def extract_pure_setting(ref_id: str, body: dict = Body(default={})):
    """ONE LLM API call over a chunk of 快捷输入 → preview lists (NOT persisted).

    Body params:
    - ``chunk_index`` (optional): when set, extract only that chunk of
      the segmented text; otherwise extract the first/only chunk.
    - ``text`` (optional): override the source text entirely (used by
      paste-back mode where the client already split). Falls back to
      reading ``quick_input_text`` from the DB.
    """
    text_override = str(body.get("text") or "").strip()
    chunk_index = int(body.get("chunk_index") or 0)

    with _conn() as con:
        row = con.execute(
            "SELECT title, creator, quick_input_text "
            "FROM reference_works WHERE ref_id = ?",
            (ref_id,),
        ).fetchone()
    if not row:
        raise HTTPException(404, "work not found")
    title = row["title"] or ""
    author = row["creator"] or ""
    full_text = (row["quick_input_text"] or "").strip()

    if text_override:
        chunk_text = text_override
        total_chunks = 1
        chunk_index = 0
    else:
        if not full_text:
            raise HTTPException(400, "快捷输入为空 — 请先粘贴 wiki 条目原文")
        chunks = _split_chunks(full_text)
        if chunk_index < 0 or chunk_index >= len(chunks):
            raise HTTPException(400, f"chunk_index 超出范围（0–{len(chunks) - 1}）")
        chunk_text = chunks[chunk_index]["text"]
        total_chunks = len(chunks)

    user_prompt, system_prompt = _render_pure_setting_prompt(
        title=title, author=author,
        chunk_index=chunk_index, total_chunks=total_chunks,
        text=chunk_text,
    )

    from llm.call_site import LLMCallSite
    cs = LLMCallSite(
        call_site_id="reference.pure_setting_extract",
        primary_role="post_commit",
        parsed_target_table="reference_works",
        default_max_tokens=3000, default_temperature=0.2,
    )
    raw = await cs.invoke(
        prompt=user_prompt,
        system=system_prompt,
        project_id=ref_id,
    )
    try:
        preview = _normalize_preview(_extract_json(raw))
    except Exception as e:
        raise HTTPException(500, f"提取结果解析失败: {e}")
    return {
        "ref_id": ref_id, "preview": True,
        "chunk_index": chunk_index, "total_chunks": total_chunks,
        **preview,
    }


@router.post("/works/{ref_id}/pure-setting/parse-paste")
def parse_pure_setting_paste(ref_id: str, body: dict = Body(...)):
    """Parse a pasted web-LLM JSON response into preview lists.

    Pure server-side normalization — no LLM call. Used by the 网页版
    extraction mode (LLM交互·机制1): the user copies the rendered prompt
    out to e.g. claude.ai, pastes the JSON response back, and the client
    POSTs the raw text here for parsing.
    """
    raw = str(body.get("raw") or "").strip()
    chunk_index = int(body.get("chunk_index") or 0)
    if not raw:
        raise HTTPException(400, "raw 为空 — 请先粘贴 LLM 返回内容")
    try:
        parsed = _extract_json(raw)
    except Exception as e:
        raise HTTPException(400, f"解析失败：{e}")
    preview = _normalize_preview(parsed)
    return {
        "ref_id": ref_id, "preview": True,
        "chunk_index": chunk_index,
        **preview,
    }
