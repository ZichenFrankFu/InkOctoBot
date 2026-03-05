from __future__ import annotations
from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .routers.reports_api import router as reports_router
from .routers.db_api import router as db_router
from .routers.analysis_api import router as analysis_router
from .routers.data_api import router as data_router

app = FastAPI(title="InkOctoBot", version="0.5.0")
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173","http://127.0.0.1:5173"],
                   allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

app.include_router(reports_router, prefix="/api")
app.include_router(db_router, prefix="/api")
app.include_router(analysis_router, prefix="/api")
app.include_router(data_router, prefix="/api")

@app.get("/health")
def health(): return {"ok": True}

STATIC_DIR = Path(__file__).resolve().parent / "static"
INDEX_HTML = STATIC_DIR / "index.html"
ASSETS_DIR = STATIC_DIR / "assets"
if ASSETS_DIR.exists():
    app.mount("/assets", StaticFiles(directory=str(ASSETS_DIR)), name="assets")

@app.get("/", include_in_schema=False)
def serve_index():
    if INDEX_HTML.exists(): return FileResponse(str(INDEX_HTML))
    return JSONResponse({"ok": False, "msg": "UI not built"}, 500)

@app.get("/{full_path:path}", include_in_schema=False)
def serve_spa(request: Request, full_path: str):
    if full_path.startswith(("api/","assets/")): return JSONResponse({"detail":"Not Found"}, 404)
    if full_path == "health": return {"ok": True}
    if INDEX_HTML.exists(): return FileResponse(str(INDEX_HTML))
    return JSONResponse({"ok": False, "msg": "UI not built"}, 500)
