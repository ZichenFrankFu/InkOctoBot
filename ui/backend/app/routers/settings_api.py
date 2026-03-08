"""
/api/settings — System settings management.

Wraps the data_api settings with additional validation and Ollama auto-detection.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ui.backend.app.settings import settings as app_settings

router = APIRouter(prefix="/api/settings", tags=["settings"])
logger = logging.getLogger("inkoctobot.ui.backend.settings_api")


def _settings_path() -> Path:
    d = app_settings.repo_root / "data"
    d.mkdir(parents=True, exist_ok=True)
    return d / "settings.json"


def _load() -> dict:
    p = _settings_path()
    if p.exists():
        data = json.loads(p.read_text("utf-8"))
    else:
        data = {}
    defaults = _defaults()
    for k, v in defaults.items():
        if k not in data:
            data[k] = v
    # Deep-merge providers and pipeline
    for pname, pdef in defaults.get("providers", {}).items():
        if pname not in data.get("providers", {}):
            data.setdefault("providers", {})[pname] = pdef
    for rname, rdef in defaults.get("pipeline", {}).items():
        if rname not in data.get("pipeline", {}):
            data.setdefault("pipeline", {})[rname] = rdef
    return data


def _save(data: dict):
    p = _settings_path()
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")


def _defaults() -> dict:
    return {
        "theme": "dark",
        "auto_save": True,
        "auto_save_interval": 30,
        "cost_confirm": True,
        "export_format": "txt",
        "providers": {
            "openai": {"enabled": False, "api_key": "", "models": ["gpt-4o", "gpt-4o-mini"]},
            "anthropic": {"enabled": False, "api_key": "", "models": ["claude-sonnet-4-5-20250929", "claude-haiku-4-5-20251001"]},
            "deepseek": {"enabled": False, "api_key": "", "models": ["deepseek-chat", "deepseek-reasoner"]},
            "gemini": {"enabled": False, "api_key": "", "models": ["gemini-2.0-flash", "gemini-2.5-pro-preview-06-05"]},
            "ollama": {"enabled": True, "base_url": "http://localhost:11434", "models": []},
            "vllm": {"enabled": False, "base_url": "http://localhost:8000", "models": []},
            "local": {"enabled": False, "models": []},
        },
        "pipeline": {
            "scene_planner": {"provider": "ollama", "model": "", "compare_models": []},
            "scene_director": {"provider": "ollama", "model": "", "compare_models": []},
            "actor_default": {"provider": "ollama", "model": "", "compare_models": []},
            "actor_protagonist": {"provider": "ollama", "model": "", "compare_models": []},
            "editor_stylist": {"provider": "ollama", "model": "", "compare_models": []},
            "editor_agent": {"provider": "ollama", "model": "", "compare_models": []},
            "evaluator": {"provider": "ollama", "model": "", "compare_models": []},
        },
    }


@router.get("")
def get_settings():
    data = _load()
    defaults = _defaults()
    for k, v in defaults.items():
        if k not in data:
            data[k] = v
    # Ensure providers have all keys
    for pname, pdef in defaults["providers"].items():
        if pname not in data.get("providers", {}):
            data.setdefault("providers", {})[pname] = pdef
    return data


@router.put("")
def update_settings(body: dict):
    import time
    body["saved_at"] = time.time()
    _save(body)
    return {"status": "ok", "saved_at": body["saved_at"]}


@router.post("/detect-ollama")
async def detect_ollama_models():
    """Auto-detect Ollama models and update settings."""
    data = _load()
    ollama_cfg = data.get("providers", {}).get("ollama", {})
    base_url = ollama_cfg.get("base_url", "http://localhost:11434")

    import httpx
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(f"{base_url}/api/tags")
            r.raise_for_status()
            models_data = r.json()
            model_names = [m["name"] for m in models_data.get("models", [])]

            # Update settings
            data.setdefault("providers", {}).setdefault("ollama", {})
            data["providers"]["ollama"]["enabled"] = True
            data["providers"]["ollama"]["models"] = model_names
            data["providers"]["ollama"]["base_url"] = base_url

            # Auto-assign first model to all pipeline roles if empty
            if model_names:
                first_model = model_names[0]
                pipeline = data.get("pipeline", {})
                for role in pipeline:
                    if not pipeline[role].get("model"):
                        pipeline[role]["provider"] = "ollama"
                        pipeline[role]["model"] = first_model

            _save(data)

            return {
                "status": "ok",
                "connected": True,
                "models": model_names,
                "message": f"检测到 {len(model_names)} 个模型",
            }
    except Exception as e:
        return {
            "status": "error",
            "connected": False,
            "models": [],
            "message": f"无法连接 Ollama ({base_url}): {str(e)[:200]}",
        }
