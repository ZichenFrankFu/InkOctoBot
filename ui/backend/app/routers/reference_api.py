# ui/backend/app/routers/reference_api.py
"""
参考作品库 API — Phase 1 placeholder

计划端点:
- GET /api/references
- POST /api/references/upload
- GET /api/references/{id}/style
"""
from fastapi import APIRouter

router = APIRouter(prefix="/references", tags=["references"])


@router.get("/status")
def references_status():
    return {
        "status": "placeholder",
        "phase": 1,
        "description": "参考作品库 API — Phase 1 实现",
        "planned_endpoints": [
            "GET /api/references",
        "POST /api/references/upload",
        "GET /api/references/{id}/style",
        ],
    }
