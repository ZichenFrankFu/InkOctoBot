# ui/backend/app/routers/characters_api.py
"""
人物卡 API — Phase 2 placeholder

计划端点:
- GET /api/characters/{project_id}
- POST /api/characters/{project_id}
- PUT /api/characters/{project_id}/{char_id}
"""
from fastapi import APIRouter

router = APIRouter(prefix="/characters", tags=["characters"])


@router.get("/status")
def characters_status():
    return {
        "status": "placeholder",
        "phase": 2,
        "description": "人物卡 API — Phase 2 实现",
        "planned_endpoints": [
            "GET /api/characters/{project_id}",
        "POST /api/characters/{project_id}",
        "PUT /api/characters/{project_id}/{char_id}",
        ],
    }
