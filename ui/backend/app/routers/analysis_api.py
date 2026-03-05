# ui/backend/app/routers/analysis_api.py
"""
分析面板 API — Phase 1 placeholder

计划端点:
- GET /api/analysis/trends
- GET /api/analysis/heatmap
- GET /api/analysis/cross_platform
"""
from fastapi import APIRouter

router = APIRouter(prefix="/analysis", tags=["analysis"])


@router.get("/status")
def analysis_status():
    return {
        "status": "placeholder",
        "phase": 1,
        "description": "分析面板 API — Phase 1 实现",
        "planned_endpoints": [
            "GET /api/analysis/trends",
        "GET /api/analysis/heatmap",
        "GET /api/analysis/cross_platform",
        ],
    }
