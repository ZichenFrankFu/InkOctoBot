"""FastAPI router: Outline, volume, and chapter planning"""
from fastapi import APIRouter

router = APIRouter(prefix="/api/planner", tags=["planner"])


@router.get("/health")
def health():
    return {"status": "ok", "router": "planner"}


# TODO: implement endpoints