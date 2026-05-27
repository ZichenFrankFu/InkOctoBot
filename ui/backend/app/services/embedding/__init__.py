"""Embedding service package (LOADER_SPEC v3.2 + EMBEDDING_SPEC).

Singleton entry point so every loader (worldbook / inspiration /
subplots / skills / reference / reader_memory) goes through the same
model + the same cache + the same hardware-adaptive layer.

Phase 1 surface:

  >>> from ui.backend.app.services.embedding import get_embedding_service
  >>> svc = get_embedding_service()
  >>> emb = svc.embed("你好世界")           # bytes
  >>> svc.get_current_model().model_key      # 'bge-base-zh'
  >>> svc.switch_model("bge-large-zh")       # returns SwitchResult

Phase 2+ wires the existing loaders into ``svc.embed`` and adds the
batch-reindex tool (see docs/EMBEDDING_SPEC.md).
"""
from .registry import (
    ModelInfo,
    default_for_language,
    get_model,
    list_models,
    models_for_language,
)
from .hardware_detector import HardwareCapabilities, detect_hardware
from .service import EmbeddingService, SwitchResult, get_embedding_service

__all__ = [
    "EmbeddingService",
    "HardwareCapabilities",
    "ModelInfo",
    "SwitchResult",
    "default_for_language",
    "detect_hardware",
    "get_embedding_service",
    "get_model",
    "list_models",
    "models_for_language",
]
