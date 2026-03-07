"""FastAPI router: API key encryption management"""
from fastapi import APIRouter

router = APIRouter(prefix="/api/security", tags=["security"])


@router.get("/health")
def health():
    return {"status": "ok", "router": "security"}


# TODO: implement endpoints