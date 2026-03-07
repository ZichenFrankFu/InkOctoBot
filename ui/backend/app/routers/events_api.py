"""FastAPI router: Agent event stream websocket and history"""
from fastapi import APIRouter

router = APIRouter(prefix="/api/events", tags=["events"])


@router.get("/health")
def health():
    return {"status": "ok", "router": "events"}


# TODO: implement endpoints