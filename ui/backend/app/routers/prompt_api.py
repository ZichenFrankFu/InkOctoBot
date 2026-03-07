"""FastAPI router: Interactive disambiguation interface"""
from fastapi import APIRouter

router = APIRouter(prefix="/api/prompt", tags=["prompt"])


@router.get("/health")
def health():
    return {"status": "ok", "router": "prompt"}


# TODO: implement endpoints