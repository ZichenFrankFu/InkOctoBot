"""User-tunable chapter / author-note / garbled-text patterns.

All persisted in ``data/settings.json`` under their respective keys
so the user can iterate without code changes. Pattern validation
compiles the regex before saving so invalid patterns are rejected
at the API layer instead of crashing the chapter parser later.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ._common import db

router = APIRouter()


# ── settings.json helpers ──────────────────────────────────────────

def _settings_path() -> Path:
    return Path(__file__).resolve().parents[5] / "data" / "settings.json"


def _read_settings() -> dict:
    p = _settings_path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def _write_settings(d: dict) -> None:
    p = _settings_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")


# ── chapter_patterns ───────────────────────────────────────────────


class ChapterPatternsBody(BaseModel):
    patterns: list[dict]


@router.get("/chapter_patterns")
def get_chapter_patterns():
    """User's custom chapter patterns. Each entry:
    ``{name: str, regex: str, enabled: bool}``. The regex should
    capture two groups (number, title); title may be omitted.
    """
    raw = _read_settings().get("chapter_patterns")
    return {"patterns": raw if isinstance(raw, list) else []}


@router.put("/chapter_patterns")
def put_chapter_patterns(body: ChapterPatternsBody):
    """Replace the entire custom-pattern list. Each entry uses either
    ``format`` (user-friendly template, preferred) or ``regex``
    (advanced). Validates regex compilation before saving."""
    from reference_pipeline.chapter_parser import format_to_regex

    cleaned: list[dict] = []
    for i, p in enumerate(body.patterns or []):
        if not isinstance(p, dict):
            raise HTTPException(400, f"第 {i + 1} 项格式错误")
        fmt = (p.get("format") or "").strip()
        regex = (p.get("regex") or "").strip()
        if not fmt and not regex:
            continue
        name = (p.get("name") or "").strip() or fmt or f"自定义 {i + 1}"
        effective = regex or format_to_regex(fmt)
        try:
            re.compile(effective)
        except re.error as e:
            raise HTTPException(400, f"「{name}」格式无效：{e}")
        entry: dict = {"name": name, "enabled": bool(p.get("enabled", True))}
        if fmt:
            entry["format"] = fmt
        if regex:
            entry["regex"] = regex
        cleaned.append(entry)
    data = _read_settings()
    data["chapter_patterns"] = cleaned
    _write_settings(data)
    return {"patterns": cleaned}


@router.delete("/chapter_patterns/{name}")
def delete_chapter_pattern(name: str):
    """Delete a custom chapter pattern by its saved name (URL-encoded).
    Built-in patterns can't be deleted via this endpoint."""
    data = _read_settings()
    raw = data.get("chapter_patterns")
    if not isinstance(raw, list):
        raise HTTPException(404, f"未找到格式「{name}」")
    before = len(raw)
    cleaned = [p for p in raw
               if isinstance(p, dict) and (p.get("name") or "").strip() != name]
    if len(cleaned) == before:
        raise HTTPException(404, f"未找到格式「{name}」（内置格式不可删除）")
    data["chapter_patterns"] = cleaned
    _write_settings(data)
    return {"patterns": cleaned, "deleted": name}


# ── author_note_keywords ───────────────────────────────────────────


def _load_author_keywords() -> list[str]:
    raw = _read_settings().get("author_note_keywords")
    if isinstance(raw, list):
        return [str(k).strip() for k in raw if k and str(k).strip()]
    return []


class AuthorKeywordsBody(BaseModel):
    keywords: list[str]


@router.get("/author_note_keywords")
def get_author_note_keywords():
    """Return both the user-customized keyword list AND the built-in
    defaults so the UI can show the active set and reset to defaults."""
    from reference_pipeline.chapter_parser import _AUTHOR_KEYWORDS
    user = _load_author_keywords()
    return {
        "user": user,
        "defaults": list(_AUTHOR_KEYWORDS),
        "active": user if user else list(_AUTHOR_KEYWORDS),
    }


@router.put("/author_note_keywords")
def put_author_note_keywords(body: AuthorKeywordsBody):
    """Replace the user's keyword list. An empty list resets to defaults."""
    cleaned = [k.strip() for k in (body.keywords or []) if k and k.strip()]
    seen: set[str] = set()
    ordered: list[str] = []
    for k in cleaned:
        if k in seen:
            continue
        seen.add(k)
        ordered.append(k)
    data = _read_settings()
    if ordered:
        data["author_note_keywords"] = ordered
    else:
        data.pop("author_note_keywords", None)
    _write_settings(data)
    from reference_pipeline.chapter_parser import _AUTHOR_KEYWORDS
    return {
        "user": ordered,
        "defaults": list(_AUTHOR_KEYWORDS),
        "active": ordered if ordered else list(_AUTHOR_KEYWORDS),
    }


# ── garbled_patterns ───────────────────────────────────────────────


class GarbledPatternsBody(BaseModel):
    patterns: list[dict]  # [{name, regex, enabled}]


@router.get("/garbled_patterns")
def get_garbled_patterns():
    """Return BOTH the built-in garbled regex set and the user's
    custom additions so the UI can show the full picture + offer
    inline disable / delete."""
    from reference_pipeline.chapter_parser import (
        _BUILTIN_GARBLED_PATTERNS, _load_user_garbled_patterns,
    )
    return {
        "builtin": [{"name": n, "regex": p} for n, p in _BUILTIN_GARBLED_PATTERNS],
        "user": _load_user_garbled_patterns(),
    }


@router.put("/garbled_patterns")
def put_garbled_patterns(body: GarbledPatternsBody):
    """Replace the user's custom garbled patterns. Each entry is
    validated by attempting to compile its regex. Empty list resets
    to defaults only (built-ins remain)."""
    cleaned: list[dict] = []
    for i, p in enumerate(body.patterns or []):
        if not isinstance(p, dict):
            raise HTTPException(400, f"第 {i + 1} 项格式错误")
        regex = (p.get("regex") or "").strip()
        if not regex:
            continue
        name = (p.get("name") or "").strip() or regex[:40]
        try:
            re.compile(regex, re.DOTALL | re.IGNORECASE)
        except re.error as e:
            raise HTTPException(400, f"「{name}」正则无效：{e}")
        cleaned.append({
            "name": name,
            "regex": regex,
            "enabled": bool(p.get("enabled", True)),
        })
    data = _read_settings()
    if cleaned:
        data["garbled_patterns"] = cleaned
    else:
        data.pop("garbled_patterns", None)
    _write_settings(data)
    return {"user": cleaned}


# ── chapter_patterns/test ──────────────────────────────────────────


class ChapterPatternTestBody(BaseModel):
    regex: str | None = None
    format: str | None = None
    pattern_name: str | None = None
    ref_id: str | None = None
    sample_text: str | None = None


@router.post("/chapter_patterns/test")
def test_chapter_pattern(body: ChapterPatternTestBody):
    """Compile + run a candidate pattern against either the given
    sample text or the (capped) full text of a specific work.

    Accepts any one of:
      - ``pattern_name``: look up built-in or custom by name
      - ``format``: user-friendly template
      - ``regex``: raw regex (advanced)
    """
    from reference_pipeline.chapter_parser import (
        format_to_regex, _PATTERNS as BUILTIN, _compile_extra,
    )
    from reference_pipeline.pipeline import _load_chapter_patterns

    regex = (body.regex or "").strip()
    fmt = (body.format or "").strip()
    pname = (body.pattern_name or "").strip()
    pat = None
    if pname:
        for n, p in BUILTIN:
            if n == pname:
                pat = p
                break
        if pat is None:
            for n, p in _compile_extra(_load_chapter_patterns()):
                if n == pname:
                    pat = p
                    break
        if pat is None:
            raise HTTPException(404, f"未找到格式「{pname}」")
    else:
        if fmt and not regex:
            regex = format_to_regex(fmt)
        if not regex:
            raise HTTPException(400, "请提供 pattern_name / format / regex")
        try:
            pat = re.compile(regex, re.MULTILINE | re.IGNORECASE)
        except re.error as e:
            raise HTTPException(400, f"正则编译失败：{e}")

    if body.sample_text:
        scan_text = body.sample_text
    elif body.ref_id:
        w = db().get_work(body.ref_id)
        if not w:
            raise HTTPException(404, "参考作品不存在")
        file_path = w.get("file_path")
        if not file_path:
            raise HTTPException(400, "尚未上传正文")
        raw_path = Path(str(file_path) + ".raw.txt")
        scan_path = raw_path if raw_path.exists() else Path(file_path)
        try:
            scan_text = scan_path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            scan_text = ""
    else:
        raise HTTPException(400, "请提供 ref_id 或 sample_text")

    ms = list(pat.finditer(scan_text))
    preview = [
        {
            "match": m.group(0)[:60],
            "groups": [g for g in m.groups()[:2]],
            "pos": m.start(),
        }
        for m in ms[:200]
    ]
    return {
        "count": len(ms), "preview": preview,
        "scanned_chars": len(scan_text), "truncated": False,
    }
