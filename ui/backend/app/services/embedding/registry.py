"""Embedding-model registry (LOADER_SPEC EMBEDDING_SPEC § 2).

Seven models, four Chinese + two multilingual/English + one legacy:

- BGE-base-zh-v1.5     [zh default]   768-dim
- BGE-large-zh-v1.5    zh             1024-dim
- Qwen3-Embedding-8B   zh             4096-dim   (large)
- Conan-Embedding-v2   zh             1792-dim
- bge-m3               multilingual   1024-dim   [en default]
- text2vec-base-multilingual multilingual  384-dim
- text2vec-base-chinese    zh         384-dim    (legacy; carries over
                                                   the v3.1 stored
                                                   embeddings until
                                                   user-driven reindex)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


Language = Literal["zh", "en", "multilingual"]


@dataclass(frozen=True)
class ModelInfo:
    """Metadata for a registered embedding model."""

    model_key: str
    display_name: str
    hf_repo: str
    language: Language
    dimension: int
    min_vram_mb: int
    min_ram_mb: int
    model_size_mb: int
    recommended_for: tuple[str, ...] = ()
    is_default_zh: bool = False
    is_default_en: bool = False


_MODELS: tuple[ModelInfo, ...] = (
    ModelInfo(
        model_key="bge-base-zh",
        display_name="BGE Base 中文 v1.5",
        hf_repo="BAAI/bge-base-zh-v1.5",
        language="zh",
        dimension=768,
        min_vram_mb=1500,
        min_ram_mb=2000,
        model_size_mb=400,
        recommended_for=("general", "first_choice"),
        is_default_zh=True,
    ),
    ModelInfo(
        model_key="bge-large-zh",
        display_name="BGE Large 中文 v1.5",
        hf_repo="BAAI/bge-large-zh-v1.5",
        language="zh",
        dimension=1024,
        min_vram_mb=3000,
        min_ram_mb=4000,
        model_size_mb=1300,
        recommended_for=("high_quality",),
    ),
    ModelInfo(
        model_key="qwen3-embedding-8b",
        display_name="Qwen3 Embedding 8B",
        hf_repo="Qwen/Qwen3-Embedding-8B",
        language="zh",
        dimension=4096,
        min_vram_mb=16000,
        min_ram_mb=20000,
        model_size_mb=16000,
        recommended_for=("sota", "large_gpu_only"),
    ),
    ModelInfo(
        model_key="conan-embedding-v2",
        display_name="Conan Embedding v2",
        hf_repo="TencentBAC/Conan-embedding-v2",
        language="zh",
        dimension=1792,
        min_vram_mb=3000,
        min_ram_mb=4000,
        model_size_mb=1400,
        recommended_for=("sota_cn",),
    ),
    ModelInfo(
        model_key="bge-m3",
        display_name="BGE M3 (multilingual)",
        hf_repo="BAAI/bge-m3",
        language="multilingual",
        dimension=1024,
        min_vram_mb=2500,
        min_ram_mb=3500,
        model_size_mb=2200,
        recommended_for=("general", "first_choice"),
        is_default_en=True,
    ),
    ModelInfo(
        model_key="text2vec-base-multilingual",
        display_name="Text2Vec Base Multilingual",
        hf_repo="shibing624/text2vec-base-multilingual",
        language="multilingual",
        dimension=384,
        min_vram_mb=800,
        min_ram_mb=1500,
        model_size_mb=450,
        recommended_for=("low_resource",),
    ),
    # Legacy — kept so v3.1 stored embeddings still match a known model
    # entry. New projects shouldn't pick this; user-triggered reindex
    # moves them to a current default.
    ModelInfo(
        model_key="text2vec-zh",
        display_name="Text2Vec 中文 (legacy)",
        hf_repo="shibing624/text2vec-base-chinese",
        language="zh",
        dimension=384,
        min_vram_mb=800,
        min_ram_mb=1500,
        model_size_mb=450,
        recommended_for=("legacy", "low_resource"),
    ),
)


# Build the lookup map at module load — the registry is immutable so
# the dict is safe to share across threads.
_BY_KEY: dict[str, ModelInfo] = {m.model_key: m for m in _MODELS}

# Defaults guarded at import time so a config drift is caught early.
_DEFAULT_ZH = next((m for m in _MODELS if m.is_default_zh), None)
_DEFAULT_EN = next((m for m in _MODELS if m.is_default_en), None)
if _DEFAULT_ZH is None:
    raise RuntimeError("registry: no model marked is_default_zh=True")
if _DEFAULT_EN is None:
    raise RuntimeError("registry: no model marked is_default_en=True")
_ZH_DEFAULTS = [m for m in _MODELS if m.is_default_zh]
_EN_DEFAULTS = [m for m in _MODELS if m.is_default_en]
if len(_ZH_DEFAULTS) != 1:
    raise RuntimeError(
        f"registry: expected exactly one zh-default model, got {len(_ZH_DEFAULTS)}"
    )
if len(_EN_DEFAULTS) != 1:
    raise RuntimeError(
        f"registry: expected exactly one en-default model, got {len(_EN_DEFAULTS)}"
    )


def list_models() -> list[ModelInfo]:
    """All registered models in declaration order."""
    return list(_MODELS)


def get_model(model_key: str) -> ModelInfo:
    """Look up a model by key. Raises ``KeyError`` on unknown key."""
    try:
        return _BY_KEY[model_key]
    except KeyError:
        valid = ", ".join(sorted(_BY_KEY))
        raise KeyError(f"unknown embedding model_key {model_key!r}; valid: {valid}")


def models_for_language(language: Language) -> list[ModelInfo]:
    """Models that match the language mode.

    ``zh`` returns Chinese + multilingual models (multilingual works for
    Chinese too). ``en`` returns multilingual + English. ``multilingual``
    returns only multilingual entries.
    """
    if language == "zh":
        return [m for m in _MODELS if m.language in ("zh", "multilingual")]
    if language == "en":
        return [m for m in _MODELS if m.language in ("en", "multilingual")]
    return [m for m in _MODELS if m.language == "multilingual"]


def default_for_language(language: Language) -> ModelInfo:
    """The is_default model for the given language mode."""
    if language == "zh":
        return _DEFAULT_ZH  # type: ignore[return-value]
    if language == "en":
        return _DEFAULT_EN  # type: ignore[return-value]
    # multilingual → reuse the EN default (bge-m3 is multilingual)
    return _DEFAULT_EN  # type: ignore[return-value]
