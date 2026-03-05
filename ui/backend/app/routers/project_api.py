# ui/backend/app/routers/project_api.py
"""
项目管理 API — Phase 2 placeholder

计划端点:
- GET /api/projects
- POST /api/projects
- GET /api/projects/{id}
- POST /api/projects/{id}/export
"""
from fastapi import APIRouter

router = APIRouter(prefix="/projects", tags=["projects"])


@router.get("/status")
def projects_status():
    return {
        "status": "placeholder",
        "phase": 2,
        "description": "项目管理 API — Phase 2 实现",
        "planned_endpoints": [
            "GET /api/projects",
        "POST /api/projects",
        "GET /api/projects/{id}",
        "POST /api/projects/{id}/export",
        ],
    }
