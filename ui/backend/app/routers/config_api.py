"""FastAPI router: Crawler config schema and save"""
from fastapi import APIRouter

router = APIRouter(prefix="/api/config", tags=["config"])


@router.get("/health")
def health():
    return {"status": "ok", "router": "config"}


# TODO: implement endpoints