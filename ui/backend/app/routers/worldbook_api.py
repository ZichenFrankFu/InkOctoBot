# ui/backend/app/routers/worldbook_api.py
"""
世界书 API — Phase 2 placeholder

计划端点:
- GET /api/worldbook/{project_id}
- PUT /api/worldbook/{project_id}
"""
from fastapi import APIRouter

router = APIRouter(prefix="/worldbook", tags=["worldbook"])


@router.get("/status")
def worldbook_status():
    return {
        "status": "placeholder",
        "phase": 2,
        "description": "世界书 API — Phase 2 实现",
        "planned_endpoints": [
            "GET /api/worldbook/{project_id}",
        "PUT /api/worldbook/{project_id}",
        ],
    }
