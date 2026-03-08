"""
/api/version — Version management and diff.

Thin wrapper over editor_api version functionality.
"""
from __future__ import annotations

import logging
from fastapi import APIRouter

router = APIRouter(prefix="/api/version", tags=["version"])
logger = logging.getLogger("inkoctobot.ui.backend.version_api")


@router.get("/health")
def health():
    return {"status": "ok", "router": "version"}
