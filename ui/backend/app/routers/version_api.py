"""FastAPI router: Version management and diff"""
from fastapi import APIRouter

router = APIRouter(prefix="/api/version", tags=["version"])


@router.get("/health")
def health():
    return {"status": "ok", "router": "version"}


# TODO: implement endpoints