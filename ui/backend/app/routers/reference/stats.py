"""Reference library aggregate statistics."""
from __future__ import annotations

from fastapi import APIRouter

from ._common import db

router = APIRouter()


@router.get("/stats")
def reference_stats():
    """Lightweight aggregate summary for the dashboard overview — counts
    only, with no per-work JSON payloads."""
    return db().stats()


@router.get("/stats/genres")
def genres():
    """Distribution of works across genres (for chart rendering)."""
    return {"genres": db().genre_distribution()}
