"""exemplars feature — pull scene-matched passages from reference_chapters.

The auto-pick path scores each stored chapter chunk against the
current chapter outline; chunks whose ``scene_type`` matches one of
the caller's ``scene_types`` get a small bonus so explicit scene
preferences win ties.

When no embeddings are stored yet (newly imported corpus), the loader
returns "" rather than emitting a useless empty section.
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any

from ._common import clip_feature, score_against_outline
from ...utils import cosine, embed_sync


_PER_PASSAGE_CHARS = 240
_TOP_N_PASSAGES = 3
_SCENE_BONUS = 0.05


def _list_chapter_rows(db_path: str, ref_id: str) -> list[dict]:
    """Best-effort list of chapter chunks with their embeddings."""
    try:
        with sqlite3.connect(db_path) as con:
            con.row_factory = sqlite3.Row
            rows = con.execute(
                "SELECT ref_id, number, title, scene_type, content, "
                "embedding_json FROM reference_chapters "
                "WHERE ref_id = ? AND COALESCE(is_author_note, 0) = 0 "
                "ORDER BY number",
                (ref_id,),
            ).fetchall()
    except sqlite3.OperationalError:
        return []
    out: list[dict] = []
    for r in rows:
        d = dict(r)
        try:
            d["embedding"] = json.loads(d.get("embedding_json") or "[]")
        except (TypeError, json.JSONDecodeError):
            d["embedding"] = []
        out.append(d)
    return out


def _rank_passages(
    rows: list[dict], outline_vec: list[float],
    scene_types: list[str] | None,
) -> list[tuple[float, dict]]:
    target_scenes = {s for s in (scene_types or []) if s}
    scored: list[tuple[float, dict]] = []
    for r in rows:
        vec = r.get("embedding") or []
        if not vec:
            continue
        sim = cosine(outline_vec, vec)
        if r.get("scene_type") and r["scene_type"] in target_scenes:
            sim += _SCENE_BONUS
        scored.append((sim, r))
    scored.sort(key=lambda t: t[0], reverse=True)
    return scored


def load_for_work(work: dict, *, chapter_outline: str = "",
                  scene_types: list[str] | None = None,
                  db_path: str = "", **_: Any) -> str:
    if not db_path or not chapter_outline.strip():
        return ""
    rows = _list_chapter_rows(db_path, work.get("ref_id") or "")
    if not rows:
        return ""
    out = embed_sync([chapter_outline])
    if not out or not out[0]:
        return ""
    outline_vec = out[0]
    scored = _rank_passages(rows, outline_vec, scene_types)
    picks = [r for _, r in scored[:_TOP_N_PASSAGES]]
    if not picks:
        return ""
    lines: list[str] = []
    for p in picks:
        head = f"（ch {p.get('number')}"
        if p.get("scene_type"):
            head += f", scene_type={p['scene_type']}"
        head += "）"
        body = (p.get("content") or "").strip()[:_PER_PASSAGE_CHARS]
        if body:
            lines.append(f"{head}{body}")
    return clip_feature("\n".join(lines))


def score_by_outline(work: dict, outline_vec: list[float]) -> float:
    """Use the best stored chunk's similarity as the work's score."""
    # We don't have db_path here; pull a best-effort path from the
    # standard location so cross-work ranking still works.
    try:
        from ui.backend.app.services.project_paths import get_db_path
        db_path = get_db_path()
    except Exception:
        return float("-inf")
    rows = _list_chapter_rows(db_path, work.get("ref_id") or "")
    if not rows or not outline_vec:
        return float("-inf")
    best = float("-inf")
    for r in rows:
        vec = r.get("embedding") or []
        if not vec:
            continue
        sim = cosine(outline_vec, vec)
        if sim > best:
            best = sim
    return best
