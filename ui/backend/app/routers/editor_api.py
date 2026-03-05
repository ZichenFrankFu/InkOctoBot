# ui/backend/app/routers/editor_api.py
"""
编辑器 API — Phase 3 placeholder

计划端点:
- GET /api/editor/chapter/{chapter_id}
- POST /api/editor/generate
- POST /api/editor/save
- GET /api/editor/versions/{chapter_id}
"""
from fastapi import APIRouter

router = APIRouter(prefix="/editor", tags=["editor"])


@router.get("/status")
def editor_status():
    return {
        "status": "placeholder",
        "phase": 3,
        "description": "编辑器 API — Phase 3 实现",
        "planned_endpoints": [
            "GET /api/editor/chapter/{chapter_id}",
        "POST /api/editor/generate",
        "POST /api/editor/save",
        "GET /api/editor/versions/{chapter_id}",
        ],
    }
