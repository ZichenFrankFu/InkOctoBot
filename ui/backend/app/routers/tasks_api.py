"""FastAPI router: Launch crawler tasks and read logs"""
from fastapi import APIRouter

router = APIRouter(prefix="/api/tasks", tags=["tasks"])


@router.get("/health")
def health():
    return {"status": "ok", "router": "tasks"}


# TODO: implement endpoints