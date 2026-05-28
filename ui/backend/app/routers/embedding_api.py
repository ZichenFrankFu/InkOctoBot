"""Embedding system API (LOADER_SPEC EMBEDDING_SPEC § 8).

- ``GET  /api/embedding/models``                  — registered model list
- ``GET  /api/embedding/current``                 — active model
- ``GET  /api/embedding/hardware-status``         — hardware + compatibility
- ``POST /api/embedding/switch``                  — change active model
- ``POST /api/embedding/reindex``                 — start reindex task
- ``GET  /api/embedding/reindex/{task_id}``       — query progress
- ``POST /api/embedding/reindex/{task_id}/cancel``— cancel task
- ``POST /api/settings/embedding-language-mode``  — toggle zh / en
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Body, HTTPException

from ..services.embedding import (
    batch_reindex, default_for_language, detect_hardware,
    get_embedding_service, get_model, list_models,
)
from ..services.embedding.hardware_detector import (
    can_run_on_gpu, decide_device,
)
from ..services.embedding.registry import ModelInfo
from ..settings import (
    get_embedding_language_mode, get_embedding_model_key,
    set_embedding_settings,
)

logger = logging.getLogger("inkoctobot.routers.embedding_api")


router = APIRouter(prefix="/api/embedding", tags=["embedding"])


def _model_to_dict(m: ModelInfo) -> dict:
    return {
        "model_key":        m.model_key,
        "display_name":     m.display_name,
        "hf_repo":          m.hf_repo,
        "language":         m.language,
        "dimension":        m.dimension,
        "min_vram_mb":      m.min_vram_mb,
        "min_ram_mb":       m.min_ram_mb,
        "model_size_mb":    m.model_size_mb,
        "recommended_for":  list(m.recommended_for),
        "is_default_zh":    m.is_default_zh,
        "is_default_en":    m.is_default_en,
    }


@router.get("/models")
def list_embedding_models() -> dict:
    """Every registered embedding model + its metadata."""
    return {"models": [_model_to_dict(m) for m in list_models()]}


@router.get("/current")
def get_current_model() -> dict:
    """The currently-active model for embeddings + readiness status.

    ``pending_warnings`` drains any post-switch warnings (e.g.
    OOM-driven CPU fallback that only surfaces on first load) so the
    UI can show them once.
    """
    svc = get_embedding_service()
    return {
        "model":             _model_to_dict(svc.get_current_model()),
        "is_ready":          svc.is_ready(),
        "dimension":         svc.get_dimension(),
        "estimate_load_seconds": svc.estimate_load_time(),
        "pending_warnings":  svc.get_and_clear_pending_warnings(),
    }


@router.get("/hardware-status")
def hardware_status() -> dict:
    """Hardware capabilities + per-model compatibility table.

    For every registered model returns the device the service WOULD
    pick under current hardware + any warnings the user should see
    before approving a switch (e.g. "will fall back to CPU").
    """
    caps = detect_hardware()
    compat: list[dict[str, Any]] = []
    for m in list_models():
        device, warnings = decide_device(m, caps)
        compat.append({
            "model_key":        m.model_key,
            "would_run_on":     device,
            "fits_gpu":         can_run_on_gpu(m, caps),
            "warnings":         warnings,
        })
    return {
        "hardware": {
            "has_cuda":         caps.has_cuda,
            "cuda_vram_mb":     caps.cuda_vram_mb,
            "cuda_device_name": caps.cuda_device_name,
            "has_mps":          caps.has_mps,
            "ram_mb":           caps.ram_mb,
            "cpu_count":        caps.cpu_count,
        },
        "model_compatibility": compat,
    }


# ─────────── write surface (Phase 3) ───────────


@router.post("/switch")
def switch_active_model(body: dict = Body(...)) -> dict:
    """Switch the active embedding model.

    Body: ``{"model_key": "..."}``. Returns the SwitchResult so the
    UI knows whether to start a reindex and what device the new model
    will run on. Persists the choice in ``data/settings.json``.
    """
    model_key = (body.get("model_key") or "").strip()
    if not model_key:
        raise HTTPException(400, "model_key is required")
    try:
        target = get_model(model_key)
    except KeyError as e:
        raise HTTPException(400, str(e))

    svc = get_embedding_service()
    result = svc.switch_model(target.model_key)
    # Persist so the next process picks it up.
    set_embedding_settings(model_key=target.model_key)
    return {
        "success":            result.success,
        "model_key":          result.model_key,
        "previous_model_key": result.previous_model_key,
        "device_decision":    result.device_decision,
        "need_reindex":       result.need_reindex,
        "affected_tables":    result.affected_tables,
        "warnings":           result.warnings,
    }


@router.post("/reindex")
def start_reindex(body: dict = Body(default={})) -> dict:
    """Start a background reindex against the active (or specified)
    embedding model. Returns the task_id for polling."""
    model_key = (body or {}).get("model_key")
    if model_key:
        try:
            get_model(model_key)
        except KeyError as e:
            raise HTTPException(400, str(e))
    task_id = batch_reindex.start_reindex(target_model_key=model_key)
    return {"task_id": task_id, "state": "running"}


@router.get("/reindex/{task_id}")
def reindex_status(task_id: str) -> dict:
    status = batch_reindex.get_status(task_id)
    if status is None:
        raise HTTPException(404, f"no reindex task with id {task_id!r}")
    return status


@router.post("/reindex/{task_id}/cancel")
def cancel_reindex(task_id: str) -> dict:
    ok = batch_reindex.cancel(task_id)
    if not ok:
        raise HTTPException(404, f"no cancellable task with id {task_id!r}")
    return {"ok": True, "task_id": task_id}


# ─────────── language-mode toggle (Phase 4) ───────────


_settings_router = APIRouter(prefix="/api/settings", tags=["embedding-settings"])


@_settings_router.post("/embedding-language-mode")
def set_language_mode(body: dict = Body(...)) -> dict:
    """Toggle the embedding language mode (``zh`` / ``en``).

    Forces the active model_key to the new mode's default — this is
    the contract from EMBEDDING_SPEC § 3 ("切 language_mode 时强制
    把 model_key 切换到对应语言的 is_default model"). Returns the
    resulting SwitchResult so the caller can decide whether to start
    a reindex.
    """
    mode = (body.get("language_mode") or "").strip()
    if mode not in ("zh", "en"):
        raise HTTPException(400, "language_mode must be 'zh' or 'en'")
    default_model = default_for_language(mode)
    svc = get_embedding_service()
    result = svc.switch_model(default_model.model_key)
    set_embedding_settings(
        language_mode=mode, model_key=default_model.model_key,
    )
    return {
        "language_mode":      mode,
        "model_key":          default_model.model_key,
        "switch_result": {
            "success":            result.success,
            "previous_model_key": result.previous_model_key,
            "device_decision":    result.device_decision,
            "need_reindex":       result.need_reindex,
            "affected_tables":    result.affected_tables,
            "warnings":           result.warnings,
        },
    }


@_settings_router.get("/embedding-language-mode")
def get_language_mode() -> dict:
    return {
        "language_mode": get_embedding_language_mode(),
        "model_key":     get_embedding_model_key(),
    }


# Re-export the settings sub-router so main.py can mount it alongside.
settings_router = _settings_router
