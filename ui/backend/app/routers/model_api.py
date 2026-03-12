"""
/api/models — Model management: list providers, test connections, list Ollama models.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/api/models", tags=["models"])
logger = logging.getLogger("inkoctobot.ui.backend.model_api")


class TestConnectionRequest(BaseModel):
    provider: str
    base_url: str = ""
    api_key: str = ""
    model: str = ""


@router.get("/providers")
def list_providers():
    """List all configured model providers and their status."""
    import json as _json
    from ui.backend.app.settings import settings
    p = settings.get_data_path("settings.json")
    if p.exists():
        data = _json.loads(p.read_text("utf-8"))
    else:
        data = {}
    providers = data.get("providers", {})
    result = []
    for name, cfg in providers.items():
        result.append({
            "id": name,
            "name": name,
            "enabled": cfg.get("enabled", False),
            "models": cfg.get("models", []),
            "has_key": bool(cfg.get("api_key")),
            "base_url": cfg.get("base_url", ""),
        })
    return {"providers": result}


@router.post("/test")
async def test_connection(req: TestConnectionRequest):
    """Test connection to a model provider."""
    if req.provider == "ollama":
        import httpx
        base = req.base_url or "http://localhost:11434"
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(f"{base}/api/tags")
                r.raise_for_status()
                data = r.json()
                models = [m["name"] for m in data.get("models", [])]
                return {
                    "status": "ok",
                    "connected": True,
                    "models": models,
                    "message": f"连接成功，发现 {len(models)} 个模型",
                }
        except Exception as e:
            return {
                "status": "error",
                "connected": False,
                "models": [],
                "message": f"连接失败: {str(e)[:200]}",
            }
    elif req.provider in ("openai", "deepseek"):
        import httpx
        base = req.base_url or ("https://api.openai.com/v1" if req.provider == "openai" else "https://api.deepseek.com/v1")
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(
                    f"{base}/models",
                    headers={"Authorization": f"Bearer {req.api_key}"},
                )
                r.raise_for_status()
                data = r.json()
                models = [m["id"] for m in data.get("data", [])][:20]
                return {
                    "status": "ok",
                    "connected": True,
                    "models": models,
                    "message": f"连接成功，发现 {len(models)} 个模型",
                }
        except Exception as e:
            return {
                "status": "error",
                "connected": False,
                "models": [],
                "message": f"连接失败: {str(e)[:200]}",
            }
    elif req.provider == "anthropic":
        if not req.api_key:
            return {"status": "error", "connected": False, "models": [], "message": "需要 API Key"}
        return {
            "status": "ok",
            "connected": True,
            "models": ["claude-sonnet-4-5-20250929", "claude-haiku-4-5-20251001"],
            "message": "API Key 已配置",
        }
    elif req.provider == "gemini":
        if not req.api_key:
            return {"status": "error", "connected": False, "models": [], "message": "需要 API Key"}
        import httpx
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(
                    "https://generativelanguage.googleapis.com/v1beta/openai/models",
                    headers={"Authorization": f"Bearer {req.api_key}"},
                )
                r.raise_for_status()
                data = r.json()
                models = [m["id"] for m in data.get("data", [])][:20]
                return {
                    "status": "ok",
                    "connected": True,
                    "models": models,
                    "message": f"连接成功，发现 {len(models)} 个模型",
                }
        except Exception as e:
            return {
                "status": "error",
                "connected": False,
                "models": [],
                "message": f"连接失败: {str(e)[:200]}",
            }
    elif req.provider == "vllm":
        import httpx
        base = req.base_url or "http://localhost:8000"
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(f"{base}/v1/models")
                r.raise_for_status()
                data = r.json()
                models = [m["id"] for m in data.get("data", [])]
                return {
                    "status": "ok",
                    "connected": True,
                    "models": models,
                    "message": f"连接成功，发现 {len(models)} 个模型",
                }
        except Exception as e:
            return {
                "status": "error",
                "connected": False,
                "models": [],
                "message": f"连接失败: {str(e)[:200]}",
            }
    elif req.provider == "mock":
        return {
            "status": "ok",
            "connected": True,
            "models": ["mock-test-v1"],
            "message": "Mock 供应商（测试模式）始终可用",
        }
    return {"status": "error", "connected": False, "models": [], "message": f"不支持的供应商: {req.provider}"}


@router.get("/ollama")
async def list_ollama_models(base_url: str = "http://localhost:11434"):
    """Auto-detect available Ollama models."""
    import httpx
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(f"{base_url}/api/tags")
            r.raise_for_status()
            data = r.json()
            models = []
            for m in data.get("models", []):
                size_gb = m.get("size", 0) / (1024**3)
                models.append({
                    "name": m["name"],
                    "size": f"{size_gb:.1f}GB",
                    "modified": m.get("modified_at", ""),
                    "family": m.get("details", {}).get("family", ""),
                    "parameters": m.get("details", {}).get("parameter_size", ""),
                    "quantization": m.get("details", {}).get("quantization_level", ""),
                })
            return {"status": "ok", "models": models, "connected": True}
    except Exception as e:
        return {"status": "error", "models": [], "connected": False, "message": str(e)[:200]}


@router.get("/cost/{project_id}")
def get_cost_summary(project_id: str):
    """Get cost estimation for a project."""
    return {
        "project_id": project_id,
        "total_input_tokens": 0,
        "total_output_tokens": 0,
        "estimated_cost_usd": 0.0,
        "sessions": [],
    }
