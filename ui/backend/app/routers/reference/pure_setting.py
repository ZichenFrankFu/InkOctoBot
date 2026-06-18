"""纯设定作品 (spec 2.2.2 / 6.2) — SCP、后室、战锤40K 等无完整正文的
众创/设定集作品。

Surfaces:
- GET/PUT /works/{ref_id}/pure-setting — structure type, 快捷输入原文,
  设定条目 (6 类 + 其他), 静态角色 (姓名/定位/描述, 不绑定章节),
  设定特征 (作品级世界观高概念/母题)
- GET /works/{ref_id}/pure-setting/segments — chunk plan; 切分对象 = 快捷
  输入文本。当快捷输入为空但已有设定/角色非空时返回一个空文本段，
  仍允许触发提取（用已有设定/角色合成 setting_features）。
- POST /works/{ref_id}/pure-setting/extract — extract one chunk (combining
  the chunk's wiki text + ALL existing settings + ALL existing characters
  into ONE prompt) via the configured LLM API, returning a PREVIEW;
  nothing persists until the client PUTs the user-pruned lists back
  (LLM交互·机制2).
- POST /works/{ref_id}/pure-setting/parse-paste — parse a pasted web-LLM
  JSON response into preview lists (LLM交互·机制1 网页版 path).

满足 LLM 交互机制：
1. API 提取 + 网页版 prompt 复制粘贴两种模式
2. 预览先入内存，逐项「确认入库」才持久化
4. 长文本按段落自动分段，每段一个独立 prompt
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

    Empty input returns []; the planning endpoint synthesizes an empty
    placeholder chunk when existing settings/characters supply context.
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

    # Any single段落 itself > max_chars: hard-cut it.
    final: list[str] = []
    for c in chunks:
        if len(c) <= max_chars:
            final.append(c)
            continue
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


def _format_existing_settings(items: list[dict]) -> str:
    """Render existing settings as compact bullet list for the prompt.

    Empty list → "（无）" so the LLM knows there's nothing to dedupe against.
    """
    if not items:
        return "（无）"
    lines: list[str] = []
    for s in items:
        if not isinstance(s, dict):
            continue
        cat = str(s.get("category") or "其他").strip()
        title = str(s.get("title") or "").strip()
        content = str(s.get("content") or "").strip()
        if not title and not content:
            continue
        # Trim long content to keep prompt tractable; LLM only needs the gist
        # to recognize duplicates, not the full description.
        snippet = content if len(content) <= 80 else content[:78] + "…"
        lines.append(f"- [{cat}] {title or '（未命名）'}: {snippet}")
    return "\n".join(lines) if lines else "（无）"


def _format_existing_characters(items: list[dict]) -> str:
    if not items:
        return "（无）"
    lines: list[str] = []
    for c in items:
        if not isinstance(c, dict):
            continue
        name = str(c.get("name") or "").strip()
        if not name:
            continue
        role = str(c.get("role") or "").strip()
        desc = str(c.get("description") or "").strip()
        snippet = desc if len(desc) <= 60 else desc[:58] + "…"
        role_part = f" [{role}]" if role else ""
        desc_part = f": {snippet}" if snippet else ""
        lines.append(f"- {name}{role_part}{desc_part}")
    return "\n".join(lines) if lines else "（无）"


def _load_work_sources(ref_id: str) -> dict:
    """Single DB read for all extract inputs."""
    with _conn() as con:
        row = con.execute(
            "SELECT title, creator, quick_input_text, settings_json, "
            "static_characters_json "
            "FROM reference_works WHERE ref_id = ?",
            (ref_id,),
        ).fetchone()
    if not row:
        raise HTTPException(404, "work not found")
    return {
        "title": row["title"] or "",
        "author": row["creator"] or "",
        "quick_input_text": (row["quick_input_text"] or "").strip(),
        "settings": _loads(row["settings_json"]),
        "characters": _loads(row["static_characters_json"]),
    }


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
    """Return the chunk plan + existing-list counts.

    Long wiki dumps need to be split into multiple LLM calls. When the
    wiki text is empty but the user has already entered settings/characters,
    we still return ONE empty-text chunk so the UI can run a single
    extraction pass (which will use the existing lists as input to
    synthesize setting_features).
    """
    src = _load_work_sources(ref_id)
    chunks = _split_chunks(src["quick_input_text"])
    has_existing = bool(src["settings"]) or bool(src["characters"])
    if not chunks and has_existing:
        chunks = [{
            "chunk_index": 0, "total_chunks": 1,
            "text": "", "n_chars": 0,
        }]
    return {
        "ref_id": ref_id,
        "title": src["title"],
        "creator": src["author"],
        "total_chars": len(src["quick_input_text"]),
        "total_chunks": len(chunks),
        "max_chunk_chars": _MAX_CHUNK_CHARS,
        "existing_settings_count": len(src["settings"]),
        "existing_characters_count": len(src["characters"]),
        "can_extract": bool(chunks),
        "chunks": [
            {
                "chunk_index": c["chunk_index"],
                "total_chunks": c["total_chunks"],
                "n_chars": c["n_chars"],
                # 仅返回前 80 字作为列表显示用，正文不重复 traffic
                "preview": c["text"][:80] if c["text"] else "（无 wiki 原文，仅用已有设定/角色）",
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
    existing_settings: list[dict], existing_characters: list[dict],
) -> tuple[str, str]:
    """Render the user prompt + return the (empty) system prompt.

    The pure-setting prompt carries every constraint inline so the
    system prompt stays empty — same shape as other reference prompts.
    The prompt now includes ALL existing settings/characters as
    deduplication context so the LLM doesn't re-extract them.
    """
    from reference_pipeline.prompts import render
    user_prompt = render(
        "reference.pure_setting",
        title=title or "",
        author=author or "",
        chunk_index_human=chunk_index + 1,
        total_chunks=max(1, total_chunks),
        n_chars=len(text),
        text=text or "（本段无 wiki 原文 — 请基于已有设定/角色合成 setting_features）",
        existing_settings_count=len(existing_settings),
        existing_settings=_format_existing_settings(existing_settings),
        existing_characters_count=len(existing_characters),
        existing_characters=_format_existing_characters(existing_characters),
    )
    return user_prompt, ""


def _plan_chunks_for_extract(src: dict) -> list[dict]:
    """Shared between extract & preview: build the chunk list that
    extraction will iterate over, including the empty-text fallback
    when only settings/characters exist."""
    chunks = _split_chunks(src["quick_input_text"])
    if not chunks and (src["settings"] or src["characters"]):
        chunks = [{
            "chunk_index": 0, "total_chunks": 1,
            "text": "", "n_chars": 0,
        }]
    return chunks


@router.post("/works/{ref_id}/pure-setting/extract")
async def extract_pure_setting(ref_id: str, body: dict = Body(default={})):
    """ONE LLM API call combining the chunk's wiki text + ALL existing
    settings + ALL existing characters → preview lists (NOT persisted).

    Body params:
    - ``chunk_index`` (optional): which chunk of the wiki text to extract.
      Defaults to 0. Out-of-range → 400.
    - ``text`` (optional): override the wiki chunk text entirely; used by
      the preview UI when the user wants to dry-run a custom string.
    """
    text_override = str(body.get("text") or "").strip()
    chunk_index = int(body.get("chunk_index") or 0)

    src = _load_work_sources(ref_id)

    if text_override:
        chunk_text = text_override
        total_chunks = 1
        chunk_index = 0
    else:
        chunks = _plan_chunks_for_extract(src)
        if not chunks:
            raise HTTPException(
                400,
                "无可处理的内容 — 请先填写「快捷输入」/「设定」/「角色」三者之一",
            )
        if chunk_index < 0 or chunk_index >= len(chunks):
            raise HTTPException(400, f"chunk_index 超出范围（0–{len(chunks) - 1}）")
        chunk_text = chunks[chunk_index]["text"]
        total_chunks = len(chunks)

    user_prompt, system_prompt = _render_pure_setting_prompt(
        title=src["title"], author=src["author"],
        chunk_index=chunk_index, total_chunks=total_chunks,
        text=chunk_text,
        existing_settings=src["settings"],
        existing_characters=src["characters"],
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
