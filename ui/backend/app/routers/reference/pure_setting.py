"""纯设定作品 (spec 2.2.2 / 6.2) — SCP、后室、战锤40K 等无完整正文的
众创/设定集作品。

数据模型：
- 「原始文本」(quick_input_text) 现在以 JSON 数组形式存储，每个元素
  {title, content} 是一个独立条目。前端可展开/收起单条；后端在跑
  特征提取前会把所有条目渲染成连续文本再分段。为保持向后兼容，
  当 quick_input_text 不是合法 JSON 时整段视为单一条目。
- 「设定特征」(setting_features) 现在带 category 字段，取值为
  核心冲突 / 高概念 / 母题 三选一。

满足 LLM 交互机制：
1. API 提取 + 网页版 prompt 复制粘贴两种模式（提取与翻译均支持）
2. 预览先入内存，逐项「确认入库」才持久化
4. 长文本按段落自动分段，每段一个独立 prompt
"""
from __future__ import annotations

import io
import json
import re
import sqlite3
from typing import Any

from fastapi import APIRouter, Body, HTTPException, UploadFile, File
from fastapi.responses import StreamingResponse

from ._common import reference_db_path

router = APIRouter(tags=["reference-pure-setting"])

STRUCTURE_TYPES = ("narrative", "setting_collection")

# 复用叙事型的 6 类设定分类 + 其他容纳异常/怪物 (spec 6.2)。
SETTING_CATEGORIES = (
    "力量体系", "势力组织", "地理", "社会规则", "历史背景", "世界观", "其他",
)

# 设定特征类别（用于按类别分组与显示）。母题已废弃，遗留数据归到「高概念」。
SETTING_FEATURE_CATEGORIES = ("核心冲突", "高概念")

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


# ── Entries (原始文本条目) ───────────────────────────────────────────


def _parse_entries(stored: str) -> list[dict]:
    """Decode `quick_input_text` into a list of {title, content} entries.

    The column now stores a JSON array; legacy plain-text values are
    coerced into a single 「条目 1」entry so old data still loads.
    """
    raw = (stored or "").strip()
    if not raw:
        return []
    try:
        data = json.loads(raw)
        if isinstance(data, list):
            out: list[dict] = []
            for item in data:
                if not isinstance(item, dict):
                    continue
                title = str(item.get("title") or "").strip()
                content = str(item.get("content") or "").strip()
                if not title and not content:
                    continue
                out.append({"title": title, "content": content})
            return out
    except (json.JSONDecodeError, TypeError):
        pass
    # legacy fallback: whole blob = one untitled entry
    return [{"title": "", "content": raw}]


def _serialize_entries(entries: list[dict]) -> str:
    clean: list[dict] = []
    for e in entries:
        if not isinstance(e, dict):
            continue
        title = str(e.get("title") or "").strip()
        content = str(e.get("content") or "").strip()
        if not title and not content:
            continue
        clean.append({"title": title, "content": content})
    return json.dumps(clean, ensure_ascii=False)


def _render_entries_for_extract(entries: list[dict]) -> str:
    """Render the entries as a continuous text the LLM can consume.

    Each entry becomes a markdown-ish section so the LLM understands
    boundaries; the splitter later prefers段落 boundaries which lines up
    with these section breaks.
    """
    parts: list[str] = []
    for i, e in enumerate(entries, 1):
        title = (e.get("title") or "").strip() or f"条目 {i}"
        content = (e.get("content") or "").strip()
        if not content and not title:
            continue
        parts.append(f"## {title}\n\n{content}".rstrip())
    return "\n\n".join(parts).strip()


# ── Chunking ─────────────────────────────────────────────────────────


def _split_chunks(text: str, max_chars: int = _MAX_CHUNK_CHARS) -> list[dict]:
    """Split text into chunks of <= max_chars, respecting段落 boundaries
    where possible. Empty input → []."""
    if not text:
        return []
    text = text.strip()
    if len(text) <= max_chars:
        return [{
            "chunk_index": 0, "total_chunks": 1,
            "text": text, "n_chars": len(text),
        }]

    paragraphs = re.split(r"\n\s*\n", text)
    if len(paragraphs) == 1:
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

    # Any single paragraph > max_chars: hard-cut, preferring sentence boundaries.
    final: list[str] = []
    for c in chunks:
        if len(c) <= max_chars:
            final.append(c)
            continue
        i = 0
        while i < len(c):
            end = min(i + max_chars, len(c))
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


# ── Context formatting for the extraction prompt ─────────────────────


def _format_existing_settings(items: list[dict]) -> str:
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
    entries = _parse_entries(row["quick_input_text"] or "")
    return {
        "title": row["title"] or "",
        "author": row["creator"] or "",
        "entries": entries,
        "rendered_text": _render_entries_for_extract(entries),
        "settings": _loads(row["settings_json"]),
        "characters": _loads(row["static_characters_json"]),
    }


# ── CRUD ─────────────────────────────────────────────────────────────


@router.get("/works/{ref_id}/pure-setting")
def get_pure_setting(ref_id: str):
    with _conn() as con:
        row = con.execute(
            "SELECT structure_type, quick_input_text, settings_json, "
            "static_characters_json, setting_features_json, updated_at "
            "FROM reference_works WHERE ref_id = ?",
            (ref_id,),
        ).fetchone()
    if not row:
        raise HTTPException(404, "work not found")
    entries = _parse_entries(row["quick_input_text"] or "")
    return {
        "ref_id": ref_id,
        "structure_type": row["structure_type"] or "narrative",
        "updated_at": row["updated_at"] or "",
        # Both shapes are returned: `raw_entries` is the new structured
        # form; `quick_input_text` keeps the old contract (the raw stored
        # string, which now happens to be JSON) for any legacy consumer.
        "raw_entries": entries,
        "quick_input_text": row["quick_input_text"] or "",
        "settings": _loads(row["settings_json"]),
        "static_characters": _loads(row["static_characters_json"]),
        "setting_features": _loads(row["setting_features_json"]),
    }


@router.put("/works/{ref_id}/pure-setting")
def update_pure_setting(ref_id: str, body: dict = Body(...)):
    """Wholesale update of any pure-setting field. Accepts either
    ``raw_entries`` (new shape — list of {title, content}) or the legacy
    ``quick_input_text`` (raw string, treated as one entry)."""
    sets: list[str] = []
    args: list[Any] = []
    if "structure_type" in body:
        st = body.get("structure_type")
        if st not in STRUCTURE_TYPES:
            raise HTTPException(400, f"structure_type must be one of {STRUCTURE_TYPES}")
        sets.append("structure_type = ?")
        args.append(st)
    if "raw_entries" in body:
        v = body.get("raw_entries")
        if not isinstance(v, list):
            raise HTTPException(400, "raw_entries must be a list")
        sets.append("quick_input_text = ?")
        args.append(_serialize_entries(v))
    elif "quick_input_text" in body:
        # legacy single-string path — wrap into one entry on the way in
        text = str(body.get("quick_input_text") or "")
        sets.append("quick_input_text = ?")
        if text.strip():
            args.append(_serialize_entries([{"title": "", "content": text}]))
        else:
            args.append("")
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


# ── Segments planning ────────────────────────────────────────────────


@router.get("/works/{ref_id}/pure-setting/segments")
def get_pure_setting_segments(ref_id: str):
    """Return the chunk plan + existing-list counts."""
    src = _load_work_sources(ref_id)
    chunks = _split_chunks(src["rendered_text"])
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
        "total_chars": len(src["rendered_text"]),
        "total_chunks": len(chunks),
        "max_chunk_chars": _MAX_CHUNK_CHARS,
        "existing_settings_count": len(src["settings"]),
        "existing_characters_count": len(src["characters"]),
        "entries_count": len(src["entries"]),
        "can_extract": bool(chunks),
        "chunks": [
            {
                "chunk_index": c["chunk_index"],
                "total_chunks": c["total_chunks"],
                "n_chars": c["n_chars"],
                "preview": c["text"][:80] if c["text"]
                           else "（无原文，仅用已有设定/角色）",
            }
            for c in chunks
        ],
    }


# ── Extraction ───────────────────────────────────────────────────────


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
        cat = str(f.get("category") or "高概念").strip()
        if cat not in SETTING_FEATURE_CATEGORIES:
            # 旧响应没有 category 时归到「高概念」
            cat = "高概念"
        features.append({
            "category": cat,
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
    from reference_pipeline.prompts import render
    user_prompt = render(
        "reference.pure_setting",
        title=title or "",
        author=author or "",
        chunk_index_human=chunk_index + 1,
        total_chunks=max(1, total_chunks),
        n_chars=len(text),
        text=text or "（本段无原文 — 请基于已有设定/角色合成 setting_features）",
        existing_settings_count=len(existing_settings),
        existing_settings=_format_existing_settings(existing_settings),
        existing_characters_count=len(existing_characters),
        existing_characters=_format_existing_characters(existing_characters),
    )
    return user_prompt, ""


def _plan_chunks_for_extract(src: dict) -> list[dict]:
    chunks = _split_chunks(src["rendered_text"])
    if not chunks and (src["settings"] or src["characters"]):
        chunks = [{
            "chunk_index": 0, "total_chunks": 1,
            "text": "", "n_chars": 0,
        }]
    return chunks


@router.post("/works/{ref_id}/pure-setting/extract")
async def extract_pure_setting(ref_id: str, body: dict = Body(default={})):
    """ONE LLM API call combining the chunk's原文 + ALL existing settings
    + ALL existing characters → preview lists (NOT persisted)."""
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
                "无可处理的内容 — 请先填写「原始文本」/「设定」/「角色」三者之一",
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
    """Parse a pasted web-LLM JSON response into preview lists."""
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


# ── Translation ──────────────────────────────────────────────────────


def _render_translate_prompt(
    *, title: str, author: str,
    chunk_index: int, total_chunks: int, text: str,
) -> tuple[str, str]:
    from reference_pipeline.prompts import render
    user_prompt = render(
        "reference.pure_setting_translate",
        title=title or "",
        author=author or "",
        chunk_index_human=chunk_index + 1,
        total_chunks=max(1, total_chunks),
        n_chars=len(text),
        text=text,
    )
    return user_prompt, ""


@router.post("/works/{ref_id}/pure-setting/translate")
async def translate_pure_setting(ref_id: str, body: dict = Body(default={})):
    """Translate one entry's content to 简体中文 via the configured LLM.

    Body params:
    - ``entry_index``: which entry to translate (required)
    - ``text`` (optional): override the entry text; useful for live editing

    Returns ``{translated}``. Caller is responsible for replacing the
    entry's content via PUT — translation never auto-persists
    (LLM交互·机制 2).
    """
    entry_index = body.get("entry_index")
    if entry_index is None:
        raise HTTPException(400, "entry_index required")
    entry_index = int(entry_index)
    text_override = str(body.get("text") or "").strip()

    src = _load_work_sources(ref_id)
    if text_override:
        text = text_override
    else:
        if entry_index < 0 or entry_index >= len(src["entries"]):
            raise HTTPException(400, "entry_index 超出范围")
        text = src["entries"][entry_index]["content"]
    if not text.strip():
        raise HTTPException(400, "条目内容为空")

    # Translation may need chunking for huge entries — preserve original
    # structure by translating chunk-by-chunk and joining with段落 breaks.
    chunks = _split_chunks(text)
    if not chunks:
        raise HTTPException(400, "条目内容为空")

    from llm.call_site import LLMCallSite
    cs = LLMCallSite(
        call_site_id="reference.pure_setting_translate",
        primary_role="post_commit",
        parsed_target_table="reference_works",
        default_max_tokens=4000, default_temperature=0.1,
    )

    translated_parts: list[str] = []
    for ci, chunk in enumerate(chunks):
        prompt, sysp = _render_translate_prompt(
            title=src["title"], author=src["author"],
            chunk_index=ci, total_chunks=len(chunks),
            text=chunk["text"],
        )
        raw = await cs.invoke(prompt=prompt, system=sysp, project_id=ref_id)
        translated_parts.append(raw.strip())
    return {
        "ref_id": ref_id,
        "entry_index": entry_index,
        "translated": "\n\n".join(translated_parts).strip(),
        "n_chunks": len(chunks),
    }


# ── Export / Import (TXT) ────────────────────────────────────────────


_EXPORT_HEADER = "# InkOctoBot 纯设定作品导出 v1"


def _render_export_txt(src: dict, features: list[dict]) -> str:
    """Render the full structured data into a human-readable TXT format.

    The format is also the import format: section markers are
    `## 原始文本 / ## 设定 / ## 角色 / ## 设定特征`; entries use
    `### 标题` lines; lists use `- [类别] 标题: 内容` style.
    """
    lines: list[str] = [
        _EXPORT_HEADER,
        f"# 标题: {src['title']}" if src["title"] else "# 标题: （未命名）",
        f"# 作者/来源: {src['author']}" if src["author"] else "# 作者/来源: （未填）",
        "",
        "## 原始文本",
    ]
    if src["entries"]:
        for i, e in enumerate(src["entries"], 1):
            lines.append("")
            lines.append(f"### {e['title'] or f'条目 {i}'}")
            lines.append("")
            lines.append(e["content"])
    else:
        lines.append("（无）")
    lines.append("")
    lines.append("## 设定")
    if src["settings"]:
        for s in src["settings"]:
            cat = s.get("category", "其他")
            title = s.get("title", "（未命名）")
            content = s.get("content", "").replace("\n", " ")
            lines.append(f"- [{cat}] {title}: {content}")
    else:
        lines.append("（无）")
    lines.append("")
    lines.append("## 角色")
    if src["characters"]:
        for c in src["characters"]:
            name = c.get("name", "")
            role = c.get("role", "")
            desc = c.get("description", "").replace("\n", " ")
            role_part = f" [{role}]" if role else ""
            lines.append(f"- {name}{role_part}: {desc}")
    else:
        lines.append("（无）")
    lines.append("")
    lines.append("## 设定特征")
    if features:
        # group by category in canonical order
        by_cat: dict[str, list[dict]] = {c: [] for c in SETTING_FEATURE_CATEGORIES}
        misc: list[dict] = []
        for f in features:
            cat = f.get("category", "高概念")
            (by_cat[cat] if cat in by_cat else misc).append(f)
        for cat in SETTING_FEATURE_CATEGORIES:
            for f in by_cat[cat]:
                desc = f.get("description", "").replace("\n", " ")
                lines.append(f"- [{cat}] {f.get('title', '')}: {desc}")
        for f in misc:
            desc = f.get("description", "").replace("\n", " ")
            lines.append(f"- [其他] {f.get('title', '')}: {desc}")
    else:
        lines.append("（无）")
    lines.append("")
    return "\n".join(lines)


@router.get("/works/{ref_id}/pure-setting/export.txt")
def export_pure_setting_txt(ref_id: str):
    """Export the whole pure-setting dataset as a downloadable TXT file."""
    src = _load_work_sources(ref_id)
    # Re-fetch features (not in _load_work_sources)
    with _conn() as con:
        row = con.execute(
            "SELECT setting_features_json FROM reference_works WHERE ref_id = ?",
            (ref_id,),
        ).fetchone()
    features = _loads(row["setting_features_json"] if row else "[]")
    body = _render_export_txt(src, features)

    # Two-part filename: an ASCII fallback (`filename=`) for legacy
    # clients + a UTF-8 encoded `filename*=` for modern browsers
    # (RFC 5987). Starlette refuses raw non-ASCII bytes in headers.
    from urllib.parse import quote
    safe_title = re.sub(r"[\\/:*?\"<>|\s]+", "_", src["title"] or "pure_setting")[:60]
    ascii_safe = re.sub(r"[^A-Za-z0-9._-]+", "_", safe_title) or "pure_setting"
    utf8_name = f"{safe_title or 'pure_setting'}.txt"
    return StreamingResponse(
        io.BytesIO(body.encode("utf-8")),
        media_type="text/plain; charset=utf-8",
        headers={
            "Content-Disposition":
                f'attachment; filename="{ascii_safe}.txt"; '
                f"filename*=UTF-8''{quote(utf8_name)}",
        },
    )


def _parse_import_txt(text: str) -> dict:
    """Parse the export-format TXT back into structured data.

    Lenient parser — only the section markers `## 原始文本 / ## 设定 /
    ## 角色 / ## 设定特征` are required; anything between them is
    interpreted in its own format.
    """
    if not text or not text.strip():
        raise ValueError("文件为空")
    # Strip optional v1 header & "# 标题"/"# 作者" leading metadata lines.
    lines = text.splitlines()

    # Find section boundaries by `## XXX` headings
    section_pattern = re.compile(r"^##\s+(原始文本|设定|角色|设定特征)\s*$")
    sections: dict[str, list[str]] = {}
    current: str | None = None
    bucket: list[str] = []
    for ln in lines:
        m = section_pattern.match(ln.strip())
        if m:
            if current is not None:
                sections[current] = bucket
            current = m.group(1)
            bucket = []
        elif current is not None:
            bucket.append(ln)
    if current is not None:
        sections[current] = bucket

    if not sections:
        raise ValueError("未识别到任何节标记（## 原始文本 / ## 设定 / ## 角色 / ## 设定特征）")

    # parse 原始文本: entries split by `### 标题`
    entries: list[dict] = []
    raw_block = sections.get("原始文本", [])
    if raw_block:
        cur_title = ""
        cur_body: list[str] = []
        for ln in raw_block:
            if ln.startswith("### "):
                if cur_title or cur_body:
                    entries.append({
                        "title": cur_title,
                        "content": "\n".join(cur_body).strip(),
                    })
                cur_title = ln[4:].strip()
                cur_body = []
            else:
                cur_body.append(ln)
        if cur_title or cur_body:
            content = "\n".join(cur_body).strip()
            if content and content != "（无）":
                entries.append({"title": cur_title, "content": content})

    # Helper for `- [类别] 标题: 内容` lines
    line_pattern = re.compile(r"^-\s*\[([^\]]+)\]\s*([^:：]+?)\s*[:：]\s*(.*)$")

    settings: list[dict] = []
    for ln in sections.get("设定", []):
        m = line_pattern.match(ln.strip())
        if not m:
            continue
        cat, title, content = m.group(1).strip(), m.group(2).strip(), m.group(3).strip()
        if cat not in SETTING_CATEGORIES:
            cat = "其他"
        settings.append({"category": cat, "title": title, "content": content})

    characters: list[dict] = []
    char_pattern = re.compile(r"^-\s*([^\[:：]+?)(?:\s*\[([^\]]+)\])?\s*[:：]\s*(.*)$")
    for ln in sections.get("角色", []):
        m = char_pattern.match(ln.strip())
        if not m:
            continue
        name = m.group(1).strip()
        if not name:
            continue
        role = (m.group(2) or "").strip()
        desc = m.group(3).strip()
        characters.append({"name": name, "role": role, "description": desc})

    features: list[dict] = []
    for ln in sections.get("设定特征", []):
        m = line_pattern.match(ln.strip())
        if not m:
            continue
        cat, title, desc = m.group(1).strip(), m.group(2).strip(), m.group(3).strip()
        if cat not in SETTING_FEATURE_CATEGORIES:
            cat = "高概念"
        features.append({"category": cat, "title": title, "description": desc})

    return {
        "raw_entries": entries,
        "settings": settings,
        "static_characters": characters,
        "setting_features": features,
    }


@router.post("/works/{ref_id}/pure-setting/import")
async def import_pure_setting_txt(
    ref_id: str,
    file: UploadFile = File(...),
    mode: str = "merge",
):
    """Import a TXT file matching the export format.

    Query/form param ``mode``:
    - ``replace`` — clobber existing entries / settings / characters / features
    - ``merge`` (default) — append unique items, keep all existing
    """
    raw = (await file.read()).decode("utf-8", errors="replace")
    try:
        parsed = _parse_import_txt(raw)
    except ValueError as e:
        raise HTTPException(400, f"解析失败：{e}")

    src = _load_work_sources(ref_id)
    # Re-fetch features as well
    with _conn() as con:
        row = con.execute(
            "SELECT setting_features_json FROM reference_works WHERE ref_id = ?",
            (ref_id,),
        ).fetchone()
    existing_features = _loads(row["setting_features_json"] if row else "[]")

    if mode == "replace":
        final = parsed
    else:
        final = {
            "raw_entries": _merge_unique(
                src["entries"], parsed["raw_entries"],
                key=lambda e: (e.get("title", "") or "") + "::" + (e.get("content", "") or "")[:40],
            ),
            "settings": _merge_unique(
                src["settings"], parsed["settings"],
                key=lambda s: f"{s.get('category')}::{s.get('title')}",
            ),
            "static_characters": _merge_unique(
                src["characters"], parsed["static_characters"],
                key=lambda c: c.get("name", ""),
            ),
            "setting_features": _merge_unique(
                existing_features, parsed["setting_features"],
                key=lambda f: f"{f.get('category')}::{f.get('title')}",
            ),
        }

    update_pure_setting(ref_id, body=final)
    return {
        "ref_id": ref_id, "mode": mode,
        "imported_entries": len(parsed["raw_entries"]),
        "imported_settings": len(parsed["settings"]),
        "imported_characters": len(parsed["static_characters"]),
        "imported_features": len(parsed["setting_features"]),
    }


def _merge_unique(
    a: list[dict], b: list[dict], *, key,
) -> list[dict]:
    seen = set()
    out: list[dict] = []
    for item in [*a, *b]:
        k = (key(item) or "").strip() if isinstance(key(item), str) else key(item)
        if k and k in seen:
            continue
        if k:
            seen.add(k)
        out.append(item)
    return out
