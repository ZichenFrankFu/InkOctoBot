"""Embedding system API (LOADER_SPEC EMBEDDING_SPEC § 8).

Phase 1 surfaces three READ-ONLY endpoints so the UI can render the
embedding settings panel without committing to a write surface yet:

- ``GET  /api/embedding/models``           — registered model list
- ``GET  /api/embedding/current``          — active model
- ``GET  /api/embedding/hardware-status``  — local hardware + per-model
                                              compatibility

Phase 3 will add the write endpoints (switch / reindex / cancel) plus
``POST /api/settings/embedding-language-mode``.
"""
from __future__ import annotations

import logging
from dataclasses import asdict
from typing import Any

from fastapi import APIRouter

from ..services.embedding import (
    detect_hardware, get_embedding_service, list_models,
)
from ..services.embedding.hardware_detector import (
    can_run_on_gpu, decide_device,
)
from ..services.embedding.registry import ModelInfo

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
    """The currently-active model for embeddings + readiness status."""
    svc = get_embedding_service()
    return {
        "model":             _model_to_dict(svc.get_current_model()),
        "is_ready":          svc.is_ready(),
        "dimension":         svc.get_dimension(),
        "estimate_load_seconds": svc.estimate_load_time(),
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
