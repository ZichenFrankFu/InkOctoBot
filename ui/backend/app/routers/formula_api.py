"""FastAPI router: Formula engine query"""
from fastapi import APIRouter

router = APIRouter(prefix="/api/formula", tags=["formula"])


@router.get("/health")
def health():
    return {"status": "ok", "router": "formula"}


# TODO: implement endpoints