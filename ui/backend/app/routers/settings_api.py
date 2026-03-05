# ui/backend/app/routers/settings_api.py
"""
系统设置 API — Phase 3 placeholder

计划端点:
- GET /api/settings
- PUT /api/settings
- POST /api/settings/apikeys
"""
from fastapi import APIRouter

router = APIRouter(prefix="/settings", tags=["settings"])


@router.get("/status")
def settings_status():
    return {
        "status": "placeholder",
        "phase": 3,
        "description": "系统设置 API — Phase 3 实现",
        "planned_endpoints": [
            "GET /api/settings",
        "PUT /api/settings",
        "POST /api/settings/apikeys",
        ],
    }
