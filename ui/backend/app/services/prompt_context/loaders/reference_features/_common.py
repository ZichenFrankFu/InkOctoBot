"""Shared helpers for the 5 reference-feature modules."""
from __future__ import annotations

import json
from typing import Any

from ...utils import cosine, embed_sync


_PER_FEATURE_BUDGET = 800  # soft per-feature clip inside a work's block


def coerce_dict(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            v = json.loads(value)
            return v if isinstance(v, dict) else {}
        except (TypeError, json.JSONDecodeError):
            return {}
    return {}


def coerce_list(value: Any) -> list:
    if isinstance(value, list):
        return value
    if isinstance(value, str) and value.strip():
        try:
            v = json.loads(value)
            return v if isinstance(v, list) else []
        except (TypeError, json.JSONDecodeError):
            return []
    return []


def clip_feature(text: str) -> str:
    text = (text or "").strip()
    if len(text) <= _PER_FEATURE_BUDGET:
        return text
    return text[:_PER_FEATURE_BUDGET].rstrip() + "……"


def _embed_text(text: str) -> list[float]:
    """One-shot embed wrapper; returns [] on failure."""
    if not text or not text.strip():
        return []
    out = embed_sync([text])
    return out[0] if out and out[0] else []


def score_against_outline(text: str, outline_vec: list[float]) -> float:
    """Score arbitrary text against a precomputed outline vector.

    Used by feature score_by_outline helpers that don't have a stored
    embedding to compare against. ``-inf`` on failure so the row sinks.
    """
    if not outline_vec:
        return float("-inf")
    vec = _embed_text(text)
    if not vec:
        return float("-inf")
    return cosine(outline_vec, vec)
