"""Developer observability endpoints.

These are gated behind ``--test`` mode or ``INKOCTO_DEBUG=1``. They
expose the in-memory log buffer, recent EventBus events, per-session
trace logs, provider/DB diagnostics and the live usage snapshot —
enough for a developer to answer "what is the app doing right now?"
without restarting it.

Mounted at ``/api/debug`` by ``ui.backend.app.main`` (no extra prefix
since this router declares its own).
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException

logger = logging.getLogger("inkoctobot.ui.backend.debug_api")

router = APIRouter(prefix="/api/debug", tags=["debug"])


def _debug_enabled() -> bool:
    """Debug endpoints are on in test mode or with INKOCTO_DEBUG=1."""
    if os.environ.get("WN_TEST_MODE") == "1":
        return True
    return bool(os.environ.get("INKOCTO_DEBUG", "").strip())


def _require_debug() -> None:
    if not _debug_enabled():
        raise HTTPException(
            403,
            "Debug endpoints disabled. Set INKOCTO_DEBUG=1 or run with --test.",
        )


@router.get("/status")
def status() -> dict[str, Any]:
    """Quick liveness probe — works even when debug is off."""
    from ui.backend.app.settings import settings as _s
    return {
        "ok": True,
        "debug_enabled": _debug_enabled(),
        "test_mode": _s.test_mode,
    }


@router.get("/recent-logs")
def recent_logs(
    limit: int = 50,
    level: str | None = None,
    logger_prefix: str | None = None,
    trace_id: str | None = None,
    session_id: str | None = None,
) -> dict[str, Any]:
    """Return the most recent log records from the in-memory ring buffer.

    Filters: ``level`` (e.g. ``INFO``), ``logger_prefix`` (e.g.
    ``inkoctobot.agents``), ``trace_id``, ``session_id``.
    """
    _require_debug()
    from framework.observability import get_buffer

    records = get_buffer().recent(
        limit=limit,
        level=level,
        logger_prefix=logger_prefix,
        trace_id=trace_id,
        session_id=session_id,
    )
    return {"count": len(records), "records": records}


@router.get("/trace/{trace_id}")
def trace_logs(trace_id: str, limit: int = 500) -> dict[str, Any]:
    """All in-memory log records that carry the given trace_id."""
    _require_debug()
    from framework.observability import get_buffer

    records = get_buffer().recent(limit=limit, trace_id=trace_id)
    return {"trace_id": trace_id, "count": len(records), "records": records}


@router.get("/session/{session_id}")
def session_logs(session_id: str, limit: int = 500) -> dict[str, Any]:
    """All in-memory log records that carry the given session_id."""
    _require_debug()
    from framework.observability import get_buffer

    records = get_buffer().recent(limit=limit, session_id=session_id)
    return {"session_id": session_id, "count": len(records), "records": records}


@router.get("/event-bus")
def event_bus_history(event_type: str | None = None, limit: int = 100) -> dict[str, Any]:
    """Return recent EventBus history, optionally filtered by event type."""
    _require_debug()
    try:
        from framework.event_bus import EventBus
        bus = EventBus.instance() if hasattr(EventBus, "instance") else None
        if bus is None:
            return {"count": 0, "events": [],
                    "note": "no active EventBus singleton"}
        events = bus.get_history(event_type=event_type, limit=limit)
        return {"count": len(events), "events": [
            {"type": e.type, "ts": getattr(e, "timestamp", None),
             "payload": getattr(e, "payload", {})}
            for e in events
        ]}
    except Exception as e:
        return {"count": 0, "events": [], "error": str(e)}


@router.get("/usage")
def usage_snapshot() -> dict[str, Any]:
    """Live usage tracker snapshot (mirrors /api/generation/usage)."""
    _require_debug()
    from ui.backend.app.services import usage_tracker
    return usage_tracker.snapshot()


@router.get("/diagnostics")
def diagnostics() -> dict[str, Any]:
    """One-shot health summary: DB sizes, log buffer stats, active sessions.

    Designed as the "show me everything that matters" endpoint for
    support: the user pastes its output, you read it.
    """
    _require_debug()
    from ui.backend.app.settings import settings as _s
    out: dict[str, Any] = {
        "repo_root": str(_s.repo_root),
        "data_dir": str(_s.data_dir) if _s.data_dir else None,
        "test_mode": _s.test_mode,
        "databases": {},
        "log_buffer": {},
    }

    base = _s.data_dir if _s.data_dir else _s.repo_root / "data"
    # Production layout after the DB rename:
    #   novels.db                  — project / chapters / memory / truth
    #   reference.db               — reference works (was inside novels.db)
    #   idea.db                    — inspirations / 灵感库 (was inside novels.db)
    #   InkOctoBot_Crawler.db      — market data, READ-ONLY from external crawler
    for name in ("novels.db", "reference.db", "idea.db", "InkOctoBot_Crawler.db"):
        p = Path(base) / name
        out["databases"][name] = {
            "exists": p.exists(),
            "bytes": p.stat().st_size if p.exists() else 0,
        }

    try:
        from framework.observability import get_buffer
        recs = get_buffer().recent(limit=10000)
        out["log_buffer"] = {
            "total_records": len(recs),
            "by_level": {
                lv: sum(1 for r in recs if r["level"] == lv)
                for lv in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
            },
        }
    except Exception as e:
        out["log_buffer"] = {"error": str(e)}

    try:
        from ui.backend.app.routers import generation_api
        active = getattr(generation_api, "_active_sessions", {})
        out["active_generation_sessions"] = len(active)
    except Exception:
        out["active_generation_sessions"] = -1

    return out


# ════════════════════════════════════════════════════════════════════
# RAG / Truth / Memory inspection — for manual calibration
# ════════════════════════════════════════════════════════════════════


@router.get("/rag-preview")
def rag_preview(
    project_id: str,
    chapter_id: str = "",
    chapter_num: int = 1,
    characters: str = "",
    web_mode: bool = False,
) -> dict[str, Any]:
    """Dump the full RAG context that WOULD be sent to the LLM.

    Returns each block separately (so you see what platform_market /
    character_cards / worldbook / reference_summary / writing_knowledge
    / writing_skills / adjacent_context / foreshadowing /
    user_preferences contributed), plus the concatenated single_agent
    prompt variable bundle, plus a token estimate.

    Use this for manual calibration: change a setting → call this
    endpoint → compare the diff to see what changed.

    Example:
        curl 'http://127.0.0.1:8713/api/debug/rag-preview?project_id=test_project_001&chapter_id=ch_test_002&chapter_num=2&characters=李星河,苏晚'
    """
    _require_debug()
    char_list = [c.strip() for c in characters.split(",") if c.strip()]

    from ui.backend.app.services.prompt_context import (
        build_generation_context,
        single_agent_vars,
        load_chapter_fields,
    )

    try:
        ctx = build_generation_context(
            project_id=project_id,
            chapter_num=chapter_num,
            characters=char_list,
            chapter_id=chapter_id,
        )
    except Exception as e:
        raise HTTPException(500, f"build_generation_context failed: {e}")

    # Per-block size breakdown for token-budget tuning.
    block_sizes = {
        name: len(body or "")
        for name, body in ctx.get("blocks", {}).items()
    }

    # Full single_agent prompt vars (what /quick-generate actually uses)
    fields = load_chapter_fields(project_id, chapter_id) if chapter_id else {}
    try:
        sa_vars = single_agent_vars(
            project_id=project_id,
            chapter_num=chapter_num,
            synopsis=fields.get("synopsis", ""),
            time_setting=fields.get("time_setting", ""),
            location=fields.get("location", ""),
            characters=char_list or fields.get("characters", []),
            existing_content=fields.get("existing_content", ""),
            chapter_id=chapter_id,
            referenced_events=fields.get("referenced_events", []),
            referenced_inspirations=fields.get("referenced_inspirations", []),
            web_mode=web_mode,
        )
    except Exception as e:
        sa_vars = {"_error": f"single_agent_vars failed: {e}"}

    # Concatenated body — what the LLM ultimately sees as the assembled
    # context block. Empty blocks vanish cleanly per the section() contract.
    assembled = "".join(
        v for v in ctx.get("blocks", {}).values() if (v or "").strip()
    )

    return {
        "project_id": project_id,
        "chapter_id": chapter_id,
        "chapter_num": chapter_num,
        "characters": char_list,
        "block_sizes": block_sizes,
        "token_estimate": ctx.get("token_estimate", 0),
        "blocks": ctx.get("blocks", {}),
        "sections": ctx.get("sections", []),
        "assembled_context": assembled,
        "single_agent_vars": sa_vars,
        "chapter_fields": fields,
    }


@router.get("/truth-files")
def truth_files_dump(project_id: str) -> dict[str, Any]:
    """Dump all 7 Truth Files for one project (raw rows + markdown view).

    For manual calibration: after each chapter generation, call this to
    verify the Writer's truth-deltas actually applied the right way.

    Returns:
      - tables: per-table row dumps (current_state / particle_ledger /
                pending_hooks + hook_events / chapter_summaries /
                subplot_threads / emotion_arcs / character_relations)
      - markdown: on-demand 7-file markdown view (the same one that
                  gets injected into prompts)
      - apply_log: idempotency log entries

    Example:
        curl 'http://127.0.0.1:8713/api/debug/truth-files?project_id=test_project_001'
    """
    _require_debug()
    from ui.backend.app.services import get_db_path
    import sqlite3

    db_path = get_db_path()
    tables: dict[str, Any] = {}

    truth_tables = [
        "truth_current_state",
        "character_ledger",
        "pending_hooks",
        "hook_events",
        "chapter_summaries",
        "subplot_threads",
        "emotion_arcs",
        "character_relations",
        "truth_apply_log",
    ]

    with sqlite3.connect(db_path) as c:
        c.row_factory = sqlite3.Row
        for t in truth_tables:
            try:
                rows = c.execute(
                    f"SELECT * FROM {t} WHERE project_id=? LIMIT 200",
                    (project_id,),
                ).fetchall()
                tables[t] = [dict(r) for r in rows]
            except sqlite3.OperationalError:
                # Table doesn't exist (older schema or not yet migrated)
                tables[t] = {"_error": "table missing"}

    # Try to also render the canonical markdown view
    markdown: dict[str, str] = {}
    try:
        from knowledge.truth.store import TruthFileStore
        from knowledge.truth.markdown_renderer import (
            render_current_state, render_pending_hooks,
            render_chapter_summaries, render_subplot_board,
            render_emotional_arcs, render_character_matrix,
            render_particle_ledger,
        )
        store = TruthFileStore(project_id=project_id, db_path=db_path)
        markdown["current_state"] = render_current_state(store)
        markdown["pending_hooks"] = render_pending_hooks(store)
        markdown["chapter_summaries"] = render_chapter_summaries(store)
        markdown["subplot_board"] = render_subplot_board(store)
        markdown["emotional_arcs"] = render_emotional_arcs(store)
        markdown["character_matrix"] = render_character_matrix(store)
        markdown["particle_ledger"] = render_particle_ledger(store)
    except Exception as e:
        markdown["_render_error"] = str(e)

    return {
        "project_id": project_id,
        "db_path": db_path,
        "tables": tables,
        "markdown": markdown,
    }


@router.get("/memory")
def memory_dump(
    project_id: str,
    layer: str = "all",
    query: str = "",
    chapter_num: int = 0,
) -> dict[str, Any]:
    """Dump the 4-layer memory state for one project.

    ``layer`` ∈ {L1, L2, L3, L4, all}. Defaults to all.

    - L1 (Immediate): scene-level in-memory buffer (only populated
                      mid-generation; usually empty when polled)
    - L2 (ChapterBuffer): recent chapter summaries from
                          ``chapter_summaries`` table
    - L3 (SemanticMemory): ChromaDB query results. Pass ``query=...``
                           to run a search; otherwise reports collection
                           stats only.
    - L4 (EpisodicTimeline): event graph + foreshadow status from
                              ``episodic_events`` + unresolved
                              foreshadowing list.

    Plus ``project_memory`` (the user-confirmed shared memory file).
    Plus knowledge isolation: ``information_events`` rows that filter
    each character's view.

    Example:
        # All layers
        curl 'http://127.0.0.1:8713/api/debug/memory?project_id=test_project_001'
        # L3 semantic search
        curl 'http://127.0.0.1:8713/api/debug/memory?project_id=test_project_001&layer=L3&query=苏晚的旧伤'
    """
    _require_debug()
    from ui.backend.app.services import get_db_path
    import sqlite3
    import json

    db_path = get_db_path()
    out: dict[str, Any] = {
        "project_id": project_id,
        "db_path": db_path,
        "layer": layer,
    }

    want_all = layer == "all"

    # L2 — chapter_summaries
    if want_all or layer == "L2":
        with sqlite3.connect(db_path) as c:
            c.row_factory = sqlite3.Row
            try:
                rows = c.execute(
                    "SELECT summary_id, chapter_num, summary_text, "
                    "key_events_json, character_states_json, is_active, created_at "
                    "FROM chapter_summaries WHERE project_id=? "
                    "ORDER BY chapter_num", (project_id,),
                ).fetchall()
                out["L2_chapter_buffer"] = [dict(r) for r in rows]
            except sqlite3.OperationalError as e:
                out["L2_chapter_buffer"] = {"_error": str(e)}

    # L3 — semantic memory
    if want_all or layer == "L3":
        try:
            from ui.backend.app.settings import settings as _s
            chromadb_dir = (_s.data_dir or _s.repo_root / "data") / "chromadb"
            from knowledge.memory.semantic_store import SemanticMemory
            sm = SemanticMemory(str(chromadb_dir))
            l3: dict[str, Any] = {
                "collection_count": sm.count(),
                "chromadb_dir": str(chromadb_dir),
            }
            if query:
                hits = sm.query(query, project_id, n_results=10)
                l3["query"] = query
                l3["hits"] = hits
            out["L3_semantic_memory"] = l3
        except Exception as e:
            out["L3_semantic_memory"] = {"_error": str(e)}

    # L4 — episodic timeline. Foreshadowing now lives in Truth Files
    # (pending_hooks + hook_events) — see docs/SCHEMA_REDESIGN.md.
    # We surface both here so callers don't have to query two endpoints.
    if want_all or layer == "L4":
        with sqlite3.connect(db_path) as c:
            c.row_factory = sqlite3.Row
            try:
                rows = c.execute(
                    "SELECT event_id, chapter_num, scene_index, event_type, "
                    "description, characters_json, importance "
                    "FROM episodic_events WHERE project_id=? "
                    "ORDER BY chapter_num, scene_index", (project_id,),
                ).fetchall()
                events = [dict(r) for r in rows]

                # Foreshadowing read from pending_hooks (the canonical store).
                try:
                    hook_rows = c.execute(
                        "SELECT hook_id, description, "
                        "origin_chapter AS chapter_num, "
                        "expected_payoff_chapter, importance, status "
                        "FROM pending_hooks WHERE project_id=? "
                        "AND status IN ('open','progressing','pressured','near_payoff') "
                        "ORDER BY origin_chapter",
                        (project_id,),
                    ).fetchall()
                    unresolved = [dict(r) for r in hook_rows]
                except sqlite3.OperationalError:
                    unresolved = []

                out["L4_episodic_timeline"] = {
                    "events": events,
                    "unresolved_foreshadowing": unresolved,
                    "event_count": len(events),
                }
            except sqlite3.OperationalError as e:
                out["L4_episodic_timeline"] = {"_error": str(e)}

    # Knowledge isolation — information_events
    if want_all:
        with sqlite3.connect(db_path) as c:
            c.row_factory = sqlite3.Row
            try:
                rows = c.execute(
                    "SELECT character_name, fact_key, knowledge_state, "
                    "believed_value, true_value, source_chapter, "
                    "source_description "
                    "FROM information_events WHERE project_id=? "
                    "ORDER BY character_name, source_chapter",
                    (project_id,),
                ).fetchall()
                out["knowledge_isolation"] = [dict(r) for r in rows]
            except sqlite3.OperationalError as e:
                out["knowledge_isolation"] = {"_error": str(e)}

    # Project memory (confirmed facts, the user-curated shared store).
    # v2.1: read from the project_memories table (was a JSON file in v1).
    if want_all:
        try:
            from ui.backend.app.services import project_store
            out["project_memory"] = project_store.get_project_memory(
                db_path, project_id,
            )
        except Exception as e:
            out["project_memory"] = {"_error": str(e)}

    return out
