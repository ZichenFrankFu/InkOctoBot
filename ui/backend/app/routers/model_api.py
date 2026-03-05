# ui/backend/app/routers/model_api.py
"""
模型管理 API — Phase 3 placeholder

计划端点:
- GET /api/models/providers
- POST /api/models/test
- GET /api/models/cost/{project_id}
"""
from fastapi import APIRouter

router = APIRouter(prefix="/models", tags=["models"])


@router.get("/status")
def models_status():
    return {
        "status": "placeholder",
        "phase": 3,
        "description": "模型管理 API — Phase 3 实现",
        "planned_endpoints": [
            "GET /api/models/providers",
        "POST /api/models/test",
        "GET /api/models/cost/{project_id}",
        ],
    }
