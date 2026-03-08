"""
/api/editor — Editor API for chapter content, versions, and text operations.
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ui.backend.app.settings import settings as app_settings

router = APIRouter(prefix="/api/editor", tags=["editor"])
logger = logging.getLogger("inkoctobot.ui.backend.editor_api")


def _versions_dir(project_id: str = "default") -> Path:
    d = app_settings.repo_root / "data" / "versions" / project_id
    d.mkdir(parents=True, exist_ok=True)
    return d


class SaveVersionRequest(BaseModel):
    project_id: str = "default"
    chapter_id: str
    text: str
    source: str = "user_edited"
    model_used: str = ""


class WordCountRequest(BaseModel):
    text: str


@router.get("/status")
def editor_status():
    return {"status": "ok", "router": "editor"}


@router.get("/versions/{chapter_id}")
def get_versions(chapter_id: str, project_id: str = "default"):
    """List all versions for a chapter."""
    vdir = _versions_dir(project_id)
    versions = []
    for f in sorted(vdir.glob(f"{chapter_id}_v*.json")):
        try:
            data = json.loads(f.read_text("utf-8"))
            versions.append(data)
        except Exception:
            pass
    return {"versions": sorted(versions, key=lambda v: v.get("version", 0), reverse=True)}


@router.post("/save-version")
def save_version(req: SaveVersionRequest):
    """Save a new version of chapter text."""
    vdir = _versions_dir(req.project_id)

    # Find next version number
    existing = list(vdir.glob(f"{req.chapter_id}_v*.json"))
    max_ver = 0
    for f in existing:
        try:
            d = json.loads(f.read_text("utf-8"))
            max_ver = max(max_ver, d.get("version", 0))
        except Exception:
            pass

    ver = max_ver + 1
    version_data = {
        "version_id": f"v_{uuid.uuid4().hex[:8]}",
        "chapter_id": req.chapter_id,
        "version": ver,
        "source": req.source,
        "text": req.text,
        "model_used": req.model_used,
        "word_count": len(req.text.replace(" ", "").replace("\n", "")),
        "created_at": time.time(),
    }

    fp = vdir / f"{req.chapter_id}_v{ver:04d}.json"
    fp.write_text(json.dumps(version_data, ensure_ascii=False, indent=2), "utf-8")

    return {"status": "ok", "version": version_data}


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
