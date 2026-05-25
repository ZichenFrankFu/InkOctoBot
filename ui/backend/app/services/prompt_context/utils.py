"""Shared helpers used by every loader.

- ``clip``    — soft-truncate a string at a character budget
- ``section`` — wrap a non-empty body into a ``## 标题`` block
- ``coerce_json`` — parse a value that may be a JSON string, dict, list or None
- ``parse_rag_excludes`` — turn ``["block::id", ...]`` into ``{block: {ids}}``
"""
from __future__ import annotations

import json
from typing import Any


def clip(text: str, limit: int) -> str:
    """Trim text to a soft character budget with an explicit marker."""
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "\n……（内容过长，已截断）"


def section(label: str, body: str) -> str:
    """Wrap a non-empty body as a labeled block, or return ""."""
    body = (body or "").strip()
    if not body:
        return ""
    return f"\n\n## {label}\n{body}"


def coerce_json(value: Any) -> Any:
    """Parse a value that may be a JSON string, dict, list or None.

    Returns None on any failure — callers should fall back gracefully
    rather than treat a malformed JSON string as fatal.
    """
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        try:
            return json.loads(s)
        except Exception:
            return None
    return None


def parse_rag_excludes(rag_excludes: list[str] | None) -> dict[str, set[str]]:
    """Parse ``["block::id", ...]`` user de-selections into ``{block: {ids}}``."""
    excl: dict[str, set[str]] = {}
    for item in (rag_excludes or []):
        s = str(item or "")
        if "::" not in s:
            continue
        blk, _, iid = s.partition("::")
        excl.setdefault(blk.strip(), set()).add(iid.strip())
    return excl
