"""
/api/editor — Editor utility endpoints (diff + word count) + version
shim that delegates to project_store / text_versions DB.

History: this router used to write per-project JSON files under
``data/versions/<pid>/`` and had a path-traversal vulnerability
(unsanitized ``project_id`` / ``chapter_id`` in a Path) plus stored
versions outside the canonical ``text_versions`` table. Both fixed
in v2.1 — endpoints now route through ``project_store``.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter
from pydantic import BaseModel

from ui.backend.app.services import project_store
from ui.backend.app.services.project_paths import get_db_path

router = APIRouter(prefix="/api/editor", tags=["editor"])
logger = logging.getLogger("inkoctobot.ui.backend.editor_api")


class SaveVersionRequest(BaseModel):
    project_id: str = "default"
    chapter_id: str
    text: str
    source: str = "user_edit"
    model_used: str = ""


class WordCountRequest(BaseModel):
    text: str


@router.get("/status")
def editor_status():
    return {"status": "ok", "router": "editor"}


@router.get("/versions/{chapter_id}")
def get_versions(chapter_id: str, project_id: str = "default"):
    """List all versions for a chapter. v2.1: DB-backed via text_versions."""
    versions = project_store.list_versions(
        get_db_path(), project_id, chapter_id=chapter_id,
    )
    # Match legacy field name 'version' (= version_num) for any old caller.
    for v in versions:
        v.setdefault("version", v.get("version_num"))
    return {"versions": versions}


@router.post("/save-version")
async def save_version(req: SaveVersionRequest):
    """Save a new version of chapter text. v2.1: DB-backed.

    After the INSERT succeeds, fires the post-commit pipeline
    (chapter_summarizer / event_extractor / chromadb_indexer /
    state_extractor / snapshot_detector). Fire-and-forget — commit
    returns immediately, pipeline runs in the background.
    """
    project_store.ensure_project_row(get_db_path(), req.project_id)
    saved = project_store.insert_version(get_db_path(), req.project_id, {
        "chapter_id": req.chapter_id,
        "source": req.source,
        "content": req.text,
        "model_used": req.model_used,
    })
    saved["version"] = saved.get("version_num")

    from ui.backend.app.services.commit_pipeline import (
        fire_and_forget_from_handler,
    )
    fire_and_forget_from_handler(
        project_id=req.project_id,
        chapter_id=req.chapter_id,
        db_path=get_db_path(),
        chapter_text=req.text,
    )
    return {"status": "ok", "version": saved}


@router.post("/diff")
def compute_diff(body: dict):
    """Compute diff between two text versions."""
    text_a = body.get("text_a", "")
    text_b = body.get("text_b", "")
    import difflib
    diff = list(difflib.unified_diff(
        text_a.splitlines(keepends=True),
        text_b.splitlines(keepends=True),
        fromfile="版本A", tofile="版本B",
        lineterm="",
    ))
    return {"diff": "\n".join(diff), "lines_changed": len([d for d in diff if d.startswith("+") or d.startswith("-")])}


@router.post("/word-count")
def word_count(req: WordCountRequest):
    """Count Chinese characters (excluding punctuation and spaces)."""
    import re
    text = req.text
    # Remove whitespace and common punctuation
    cleaned = re.sub(r'[\s\p{P}]', '', text, flags=re.UNICODE) if text else ""
    return {"count": len(cleaned), "raw_length": len(text)}
