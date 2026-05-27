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
from .routers.embedding_api import router as embedding_router
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
app.include_router(embedding_router)  # /api/embedding (read-only Phase 1)

# Dev tools (actions: health-check, seed-test-data)
app.include_router(dev_router, prefix="/api")
# Observability / debug (read-only: logs, traces, diagnostics)
app.include_router(debug_router)


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
