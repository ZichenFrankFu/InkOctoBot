"""
/api/security — API key management.
"""
from __future__ import annotations

import logging
from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(prefix="/api/security", tags=["security"])
logger = logging.getLogger("inkoctobot.ui.backend.security_api")


@router.get("/health")
def health():
    return {"status": "ok", "router": "security"}


@router.get("/key-status")
def key_status():
    """Check which API keys are configured."""
    import json as _json
    from ui.backend.app.settings import settings
    p = settings.repo_root / "data" / "settings.json"
    result = {}
    if p.exists():
        data = _json.loads(p.read_text("utf-8"))
        for name, cfg in data.get("providers", {}).items():
            result[name] = {
                "has_key": bool(cfg.get("api_key")),
                "enabled": cfg.get("enabled", False),
            }
    return {"keys": result}
