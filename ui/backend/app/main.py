from __future__ import annotations
from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

# ── Core data routers ──
from .routers.reports_api import router as reports_router
from .routers.market_db_api import router as db_router
from .routers.analysis_api import router as analysis_router
from .routers.json_storage_api import router as data_router
from .routers.reference_api import router as reference_router
from .routers.extraction_api import router as extraction_router
from .routers.marketing_api import router as marketing_router

# ── Agent pipeline routers ──
from .routers.generation_api import router as generation_router
from .routers.planner_api import router as planner_router
from .routers.events_api import router as events_router
from .routers.prompt_api import router as prompt_router

# ── Editor & content routers ──
from .routers.editor_api import router as editor_router
from .routers.evaluation_api import router as eval_router
from .routers.version_api import router as version_router

# ── Management routers ──
from .routers.model_api import router as model_router
from .routers.settings_api import router as settings_router
from .routers.characters_api import router as characters_router
from .routers.worldbook_api import router as worldbook_router
from .routers.security_api import router as security_router
from .routers.project_api import router as project_router
from .routers.skill_api import router as skill_router
from .routers.dev_actions_api import router as dev_router
from .routers.entity_api import router as entity_router
from .routers.snapshot_api import (
    router as snapshot_router,
    reminder_router as snapshot_reminder_router,
)
from .routers.embedding_api import (
    router as embedding_router,
    settings_router as embedding_settings_router,
)
from .routers.commit_pipeline_api import router as commit_pipeline_router
from .routers.notifications_api import router as notifications_router
from .routers.preferences_api import router as preferences_router
from .routers.rollback_api import router as rollback_router
from .routers.genesis_api import router as genesis_router
from .routers.storyland_api import router as storyland_router
from .routers.knowledge_api import router as knowledge_router
from .routers.state_review_api import router as state_review_router
from .routers.historical_view_api import router as historical_view_router
from .routers.validator_api import router as validator_router
from .routers.market_extractor_api import (
    router as market_extractor_router,
    profiles_router as platform_profiles_router,
)
from .routers.llm_paste_api import router as llm_paste_router
from .routers.llm_audit_api import router as llm_audit_router
from .routers.debug_api import router as debug_router

app = FastAPI(title="InkOctoBot — AI 小说智能体工作台", version="2.1.0")
# Bind a trace_id per HTTP request so every log line emitted while
# handling the request is correlatable; echoed back via X-Request-ID.
from framework.observability.request_middleware import TraceIDMiddleware
app.add_middleware(TraceIDMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173", "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register all routers
app.include_router(reports_router, prefix="/api")
app.include_router(db_router, prefix="/api")
app.include_router(analysis_router, prefix="/api")
app.include_router(data_router, prefix="/api")
app.include_router(reference_router, prefix="/api")
app.include_router(extraction_router, prefix="/api")
app.include_router(marketing_router, prefix="/api")

# Agent pipeline
app.include_router(generation_router)
app.include_router(planner_router)
app.include_router(events_router)
app.include_router(prompt_router)

# Editor & content
app.include_router(editor_router)
app.include_router(eval_router)
app.include_router(version_router)

# Management
app.include_router(model_router)
app.include_router(settings_router)
app.include_router(characters_router)
app.include_router(worldbook_router)
app.include_router(security_router)
app.include_router(project_router)
app.include_router(skill_router)
app.include_router(entity_router)  # /api/entities (prefix already set on the router)
app.include_router(snapshot_router)  # /api/snapshots
app.include_router(snapshot_reminder_router)  # /api/snapshot-reminders
app.include_router(embedding_router)  # /api/embedding (Phase 1 + 3)
app.include_router(embedding_settings_router)  # /api/settings/embedding-language-mode
app.include_router(commit_pipeline_router)  # /api/commit-pipeline
app.include_router(notifications_router)  # /api/notifications
app.include_router(preferences_router)  # /api/preferences (自学习偏好确认 gate)
app.include_router(rollback_router)  # /api/rollback (事务性回溯)
app.include_router(genesis_router)  # /api/genesis (Storyland 创世)
app.include_router(storyland_router)  # /api/storyland (状态/故事线数据面)
app.include_router(knowledge_router)  # /api/knowledge (专业知识自学习)
app.include_router(state_review_router)  # /api/state-review + manual fallback CRUD
app.include_router(historical_view_router)  # /api/historical-view
app.include_router(validator_router)  # /api/validator
app.include_router(market_extractor_router)  # /api/market-extractor
app.include_router(platform_profiles_router)  # /api/platform-profiles
app.include_router(llm_paste_router)  # /api/llm-paste (manual mode inbox)
app.include_router(llm_audit_router)  # /api/llm-audit (unified audit view)

# Dev tools (actions: health-check, seed-test-data)
app.include_router(dev_router, prefix="/api")
# Observability / debug (read-only: logs, traces, diagnostics)
app.include_router(debug_router)


@app.on_event("startup")
def _stage5_mark_interrupted_pipeline_sessions() -> None:
    """Stage 5: on every boot, flip ``status='running'`` /
    ``'paused_audit_review'`` rows in ``pipeline_sessions`` to
    ``'interrupted'`` — the asyncio.Task that was driving them is
    gone, the row stays for the history view."""
    try:
        from .services import pipeline_session_store
        from .services.project_paths import get_db_path
        db_path = get_db_path()
        if not db_path:
            return
        n = pipeline_session_store.mark_running_as_interrupted(db_path)
        if n:
            import logging
            logging.getLogger("inkoctobot.main").info(
                "marked %d stale pipeline session(s) as interrupted", n,
            )
    except Exception:
        import logging
        logging.getLogger("inkoctobot.main").exception(
            "stage 5 startup hook failed",
        )


@app.get("/health")
def health():
    from .settings import settings as _s
    return {"ok": True, "version": "2.0.0", "test_mode": _s.test_mode}


STATIC_DIR = Path(__file__).resolve().parent / "static"
INDEX_HTML = STATIC_DIR / "index.html"
ASSETS_DIR = STATIC_DIR / "assets"
if ASSETS_DIR.exists():
    app.mount("/assets", StaticFiles(directory=str(ASSETS_DIR)), name="assets")


@app.get("/", include_in_schema=False)
def serve_index():
    if INDEX_HTML.exists():
        return FileResponse(str(INDEX_HTML))
    return JSONResponse({"ok": False, "msg": "UI not built"}, 500)


@app.get("/{full_path:path}", include_in_schema=False)
def serve_spa(request: Request, full_path: str):
    if full_path.startswith(("api/", "assets/")):
        return JSONResponse({"detail": "Not Found"}, 404)
    if full_path == "health":
        return {"ok": True}
    # Serve static files (e.g. favicon.svg) directly if they exist
    static_file = STATIC_DIR / full_path
    if static_file.is_file() and STATIC_DIR in static_file.resolve().parents:
        return FileResponse(str(static_file))
    if INDEX_HTML.exists():
        return FileResponse(str(INDEX_HTML))
    return JSONResponse({"ok": False, "msg": "UI not built"}, 500)
