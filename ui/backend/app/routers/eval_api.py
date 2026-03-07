"""FastAPI router: Evaluation and edit analyzer"""
from fastapi import APIRouter

router = APIRouter(prefix="/api/eval", tags=["eval"])


@router.get("/health")
def health():
    return {"status": "ok", "router": "eval"}


# TODO: implement endpoints