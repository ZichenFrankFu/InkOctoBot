"""/api/domain-learning — gate-1 and gate-2 endpoints for Part B.

Lifecycle a row passes through:

    proposed   ── approve(api)     ──► compiling ── compile_api ──► needs_review
               ── approve(manual)  ──► compiling ── submit_manual ─► needs_review
               ── reject           ──► rejected
    needs_review ── accept ──► accepted  (writes SKILL.md + registers)
                 ── reject ──► rejected
    accepted   ── PUT content ──► accepted (re-writes + re-embeds)

The /suggest endpoint is a manual trigger for the user to force a
batch extraction (and therefore a domain probe) without waiting for
N edits to accumulate."""
from __future__ import annotations

import logging
import sqlite3
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ui.backend.app.services.project_paths import get_db_path

router = APIRouter(prefix="/api/domain-learning", tags=["domain-learning"])
logger = logging.getLogger("inkoctobot.ui.backend.domain_learning_api")


# ─────────── DTOs ───────────


class ApproveRequest(BaseModel):
    mode: str  # 'api' | 'manual'


class SubmitManualRequest(BaseModel):
    content: str


class AcceptRequest(BaseModel):
    edited_content: str | None = None


class EditAcceptedRequest(BaseModel):
    new_content: str


class SuggestRequest(BaseModel):
    project_id: str


# ─────────── helpers ───────────


def _open() -> sqlite3.Connection:
    con = sqlite3.connect(get_db_path())
    con.row_factory = sqlite3.Row
    return con


def _row_to_payload(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    # Don't send full compiled_content in the list view — it can be
    # large; the /review endpoint hands it over when needed.
    d["compiled_content_preview"] = (d.get("compiled_content") or "")[:300]
    d["has_compiled_content"] = bool(d.get("compiled_content"))
    return d


def _fetch(suggestion_id: str) -> dict[str, Any] | None:
    with _open() as con:
        row = con.execute(
            "SELECT * FROM domain_suggestions WHERE suggestion_id = ?",
            (suggestion_id,),
        ).fetchone()
    return dict(row) if row else None


# ─────────── endpoints ───────────


@router.get("/suggestions")
def list_suggestions(project_id: str, status: str = "") -> dict:
    """List suggestions for a project, optionally filtered by status."""
    q = "SELECT * FROM domain_suggestions WHERE project_id = ?"
    params: list[Any] = [project_id]
    if status:
        q += " AND status = ?"
        params.append(status)
    q += " ORDER BY updated_at DESC"
    with _open() as con:
        rows = con.execute(q, params).fetchall()
    return {"suggestions": [_row_to_payload(r) for r in rows]}


@router.get("/{suggestion_id}/review")
def get_review(suggestion_id: str) -> dict:
    """Gate-2 preview — returns the full compiled_content."""
    row = _fetch(suggestion_id)
    if not row:
        raise HTTPException(404, "suggestion not found")
    return {
        "suggestion_id":    suggestion_id,
        "status":           row["status"],
        "domain":           row["domain"],
        "sub_domain":       row.get("sub_domain") or "",
        "rationale":        row.get("rationale") or "",
        "source_mode":      row.get("source_mode") or "",
        "compiled_content": row.get("compiled_content") or "",
        "skill_name":       row.get("skill_name") or "",
    }


@router.post("/{suggestion_id}/reject")
def reject(suggestion_id: str) -> dict:
    """Gate-1 reject OR gate-2 discard. Both flip to ``rejected``."""
    row = _fetch(suggestion_id)
    if not row:
        raise HTTPException(404, "suggestion not found")
    with _open() as con:
        con.execute(
            "UPDATE domain_suggestions SET status='rejected', "
            "updated_at=strftime('%s','now') WHERE suggestion_id=?",
            (suggestion_id,),
        )
        con.commit()
    return {"status": "rejected"}


@router.post("/{suggestion_id}/approve")
async def approve(suggestion_id: str, body: ApproveRequest) -> dict:
    """Gate 1 approve. ``mode='api'`` kicks the web-search compile;
    ``mode='manual'`` returns a research-instruction prompt the user
    pastes into a browser LLM and brings back via /submit-manual."""
    row = _fetch(suggestion_id)
    if not row:
        raise HTTPException(404, "suggestion not found")
    if row["status"] != "proposed":
        raise HTTPException(
            409, f"cannot approve from status={row['status']!r}",
        )

    mode = (body.mode or "").strip().lower()
    if mode not in ("api", "manual"):
        raise HTTPException(400, "mode must be 'api' or 'manual'")

    if mode == "manual":
        # Mark as compiling so the row exits the proposed list; return
        # the instruction prompt for the user to take to a browser LLM.
        from agents.learning.knowledge_acquisition import (
            build_manual_research_prompt,
        )
        with _open() as con:
            con.execute(
                "UPDATE domain_suggestions SET status='compiling', "
                "source_mode='manual', updated_at=strftime('%s','now') "
                "WHERE suggestion_id=?",
                (suggestion_id,),
            )
            con.commit()
        prompt = build_manual_research_prompt(
            domain=row["domain"],
            sub_domain=row.get("sub_domain") or "",
            rationale=row.get("rationale") or "",
        )
        return {"status": "compiling", "mode": "manual",
                "research_prompt": prompt}

    # API mode — runs the LLM compile inline; the caller is expected
    # to have already cleared cost confirmation. On WebSearchUnavailable
    # the helper returns ``{"status": "failed", "hint": "manual"}`` so
    # the UI can suggest a downgrade to manual mode.
    from agents.learning.knowledge_acquisition import run_compile_api
    result = await run_compile_api(
        db_path=get_db_path(), suggestion_id=suggestion_id,
    )
    return result


@router.post("/{suggestion_id}/submit-manual")
def submit_manual(suggestion_id: str, body: SubmitManualRequest) -> dict:
    """Manual mode: user pastes their browser-LLM output here. The
    row transitions to ``needs_review`` exactly like the API path."""
    from agents.learning.knowledge_acquisition import submit_manual_result
    res = submit_manual_result(
        db_path=get_db_path(),
        suggestion_id=suggestion_id,
        pasted_content=body.content,
    )
    if res.get("status") == "missing":
        raise HTTPException(404, "suggestion not found")
    return res


@router.post("/{suggestion_id}/accept")
def accept(suggestion_id: str, body: AcceptRequest) -> dict:
    """Gate 2 accept — writes SKILL.md and registers."""
    from agents.learning.knowledge_skill_writer import accept_suggestion
    res = accept_suggestion(
        db_path=get_db_path(),
        suggestion_id=suggestion_id,
        edited_content=body.edited_content,
    )
    if res.get("status") == "missing":
        raise HTTPException(404, "suggestion not found")
    if res.get("status") == "rejected":
        raise HTTPException(409, res.get("reason") or "cannot accept")
    return res


@router.put("/{suggestion_id}/content")
def edit_accepted(suggestion_id: str, body: EditAcceptedRequest) -> dict:
    """Edit the body of an already-accepted knowledge skill — rewrites
    the on-disk SKILL.md and triggers an embedding recomputation."""
    from agents.learning.knowledge_skill_writer import update_accepted_content
    res = update_accepted_content(
        db_path=get_db_path(),
        suggestion_id=suggestion_id,
        new_content=body.new_content,
    )
    if res.get("status") == "missing":
        raise HTTPException(404, "suggestion not found")
    if res.get("status") == "rejected":
        raise HTTPException(409, res.get("reason") or "cannot edit")
    return res


@router.post("/suggest")
def manual_suggest(body: SuggestRequest) -> dict:
    """Force a batch extraction now (bypassing the N-edit threshold)
    so the user can ask for fresh domain suggestions on demand."""
    from agents.learning.edit_batch_extractor import fire_batch_extraction
    fire_batch_extraction(db_path=get_db_path(), project_id=body.project_id)
    return {"status": "scheduled"}
