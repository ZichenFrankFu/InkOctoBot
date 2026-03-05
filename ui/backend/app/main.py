from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .routers.reports_api import router as reports_router
from .routers.db_api import router as db_router

app = FastAPI(title="InkOctoBot UI Backend", version="0.2.0")

# ===== CORS：仅开发需要（Vite dev server -> FastAPI）=====
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ===== API routers =====
app.include_router(reports_router, prefix="/api")
app.include_router(db_router, prefix="/api")

@app.get("/health")
def health():
    return {"ok": True}

# ===== React build 静态文件托管 =====
STATIC_DIR = Path(__file__).resolve().parent / "static"
INDEX_HTML = STATIC_DIR / "index.html"
ASSETS_DIR = STATIC_DIR / "assets"

if ASSETS_DIR.exists():
    app.mount("/assets", StaticFiles(directory=str(ASSETS_DIR)), name="assets")

@app.get("/", include_in_schema=False)
def serve_index():
    if INDEX_HTML.exists():
        return FileResponse(str(INDEX_HTML))
    return JSONResponse(
        {
            "ok": False,
            "msg": "UI not built yet. Run: cd ui/frontend && npm run build",
            "expected": str(INDEX_HTML),
        },
        status_code=500,
    )

@app.get("/{full_path:path}", include_in_schema=False)
def serve_spa(request: Request, full_path: str):
    if full_path.startswith("api/") or full_path == "api":
        return JSONResponse({"detail": "Not Found"}, status_code=404)
    if full_path.startswith("assets/") or full_path == "assets":
        return JSONResponse({"detail": "Not Found"}, status_code=404)
    if full_path == "health":
        return {"ok": True}

    if INDEX_HTML.exists():
        return FileResponse(str(INDEX_HTML))

    return JSONResponse(
        {
            "ok": False,
            "msg": "UI not built yet. Run: cd ui/frontend && npm run build",
            "expected": str(INDEX_HTML),
        },
        status_code=500,
    )
