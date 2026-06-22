"""EmbeddingService — process-wide singleton in front of the provider.

Owns the lifecycle (load / unload), per-process LRU cache, text hashing,
and the model-switch protocol (returning a ``SwitchResult`` so the
caller can decide whether to trigger reindex).

Phase 1 surface:

    get_embedding_service() -> EmbeddingService
    svc.embed(text)       -> bytes
    svc.embed_batch(...)  -> list[bytes]
    svc.switch_model(key) -> SwitchResult
    svc.get_current_model() -> ModelInfo

Phase 2 wires loaders into ``svc.embed``; Phase 3 adds the
``batch_reindex`` helper that consumes SwitchResult.need_reindex.
"""
from __future__ import annotations

import hashlib
import logging
import threading
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any

from .hardware_detector import (
    HardwareCapabilities, decide_device, detect_hardware,
)
from .providers import EmbeddingProvider, TransformersProvider
from .registry import (
    ModelInfo, default_for_language, get_model, list_models,
)

logger = logging.getLogger("inkoctobot.services.embedding.service")


_HASH_LEN = 16          # SHA-256 truncated for compact storage
_CACHE_SIZE = 4096


# Tables the reindex tool needs to touch when the active model changes.
# Used by ``SwitchResult.affected_tables`` so the caller can plan the
# reindex scope without re-reading the spec.
_AFFECTED_TABLES = [
    "worldbook_entries",
    "inspirations",
    "subplot_threads",
    "skill_index",
    "reference_chapters",
    "chromadb_collections",  # logical name; real path is per-collection
]


class EmbeddingBackendUnavailable(RuntimeError):
    """Raised when the local embedding stack (sentence-transformers /
    transformers / torch) is installed but broken at import time.

    Common case: the user's transformers version no longer exports
    ``EncoderDecoderCache`` (removed/renamed in newer releases), so
    sentence-transformers' ``trainer`` import chain raises. Callers
    that can degrade gracefully (commit_pipeline sub-tasks, prompt
    loaders) catch this and skip themselves; callers that need
    embeddings to function escalate to a 503.

    Hold the original cause in ``__cause__`` so the operator log still
    shows the underlying ImportError for diagnosis.
    """


_BACKEND_BROKEN_MARKERS = (
    "EncoderDecoderCache",
    "cannot import name",
    "transformers.trainer",
)


def _looks_like_broken_backend(exc: BaseException) -> bool:
    """Heuristic: is *this* exception the broken-transformers signature?

    sentence-transformers wraps the underlying ImportError in its own
    RuntimeError sometimes, so we check both the message and the chain.
    """
    cur: BaseException | None = exc
    while cur is not None:
        msg = str(cur)
        if isinstance(cur, ImportError) and any(m in msg for m in _BACKEND_BROKEN_MARKERS):
            return True
        if any(m in msg for m in _BACKEND_BROKEN_MARKERS):
            return True
        cur = cur.__cause__ or cur.__context__
    return False


@dataclass
class SwitchResult:
    """Returned by ``EmbeddingService.switch_model``."""

    success: bool
    model_key: str
    previous_model_key: str
    device_decision: str
    need_reindex: bool
    affected_tables: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class EmbeddingService:
    """Process-global embedding façade.

    Not for direct construction — call :func:`get_embedding_service`.
    """

    _instance: "EmbeddingService | None" = None
    _instance_lock = threading.Lock()

    def __init__(self, *, initial_model: ModelInfo | None = None) -> None:
        # ``_provider`` is None until the first embed() — lazy load
        # keeps process startup snappy.
        self._model: ModelInfo = initial_model or default_for_language("zh")
        self._provider: EmbeddingProvider | None = None
        self._cache: OrderedDict[str, bytes] = OrderedDict()
        self._cache_lock = threading.Lock()
        # Warnings that surface after switch_model() returns — e.g. an
        # OOM-driven CPU fallback that only happens on the first actual
        # load(). Caller drains via get_and_clear_pending_warnings().
        self._pending_warnings: list[str] = []
        self._warnings_lock = threading.Lock()

    # ─────────── public surface ───────────

    @classmethod
    def get(cls) -> "EmbeddingService":
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = cls._build_from_settings()
            return cls._instance

    @classmethod
    def _reset_for_tests(cls) -> None:
        """Test helper — drop the singleton."""
        with cls._instance_lock:
            cls._instance = None

    def get_current_model(self) -> ModelInfo:
        return self._model

    def get_dimension(self) -> int:
        return self._model.dimension

    def is_ready(self) -> bool:
        return self._provider is not None and self._provider.is_loaded

    def estimate_load_time(self) -> int:
        """Rough seconds estimate based on model_size_mb.

        Used by the UI to show a "this might take N seconds" toast on
        first switch. Assumes a 50 MB/s effective HF download speed
        (conservative) + 2-second loading overhead.
        """
        return max(2, int(self._model.model_size_mb / 50))

    # ─────────── embedding ───────────

    def embed(self, text: str) -> bytes:
        if text is None:
            return b""
        out = self.embed_batch([text])
        return out[0] if out else b""

    def embed_batch(self, texts: list[str]) -> list[bytes]:
        if not texts:
            return []
        # Cache lookup: anything we already have stays out of the batch.
        results: dict[int, bytes] = {}
        missing_idx: list[int] = []
        missing_texts: list[str] = []
        for i, text in enumerate(texts):
            key = self._cache_key(text)
            cached = self._cache_get(key)
            if cached is not None:
                results[i] = cached
            else:
                missing_idx.append(i)
                missing_texts.append(text)

        if missing_texts:
            arr = self._ensure_provider().embed(missing_texts, normalize=True)
            for j, idx in enumerate(missing_idx):
                vec_bytes = arr[j].astype("float32", copy=False).tobytes()
                results[idx] = vec_bytes
                self._cache_put(self._cache_key(texts[idx]), vec_bytes)

        return [results[i] for i in range(len(texts))]

    # ─────────── hashing ───────────

    def compute_text_hash(self, text: str) -> str:
        """SHA-256[:16] of UTF-8 bytes. Deterministic across processes."""
        return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:_HASH_LEN]

    def is_text_hash_match(self, stored: str, current_text: str) -> bool:
        return bool(stored) and stored == self.compute_text_hash(current_text)

    # ─────────── switch ───────────

    def switch_model(self, model_key: str) -> SwitchResult:
        """Swap the active model. Does NOT auto-reindex — caller decides."""
        target = get_model(model_key)
        previous = self._model
        if target.model_key == previous.model_key:
            return SwitchResult(
                success=True,
                model_key=target.model_key,
                previous_model_key=previous.model_key,
                device_decision=self._provider.get_device() if self._provider else "cpu",
                need_reindex=False,
                warnings=["model already active"],
            )

        # Tear down the old provider before we choose a device for the
        # new one — frees VRAM so the GPU decision is realistic.
        if self._provider is not None:
            self._provider.unload()
            self._provider = None

        caps = detect_hardware()
        device, warnings = decide_device(target, caps)
        # Don't load yet — provider is created here, model loads on
        # first embed(). That keeps the switch_model() call cheap.
        self._provider = TransformersProvider(
            target, device=device, fp16=(device == "cuda"),
        )
        self._model = target
        self._cache_clear()

        return SwitchResult(
            success=True,
            model_key=target.model_key,
            previous_model_key=previous.model_key,
            device_decision=device,
            # Dimension change OR model identity change → reindex.
            need_reindex=(target.dimension != previous.dimension
                           or target.model_key != previous.model_key),
            affected_tables=list(_AFFECTED_TABLES),
            warnings=warnings,
        )

    # ─────────── internals ───────────

    @classmethod
    def _build_from_settings(cls) -> "EmbeddingService":
        """Read ``data/settings.json`` for the persisted model_key and
        construct the singleton. Missing / invalid keys fall back to
        the language default."""
        try:
            from ui.backend.app.settings import (
                get_embedding_model_key,
                get_embedding_language_mode,
            )
            model_key = get_embedding_model_key()
            language = get_embedding_language_mode()
        except Exception as e:
            logger.debug("settings unavailable, using zh default: %s", e)
            model_key, language = "bge-base-zh", "zh"

        try:
            model = get_model(model_key)
        except KeyError:
            logger.warning(
                "settings reference unknown model_key %r; using %s default",
                model_key, language,
            )
            model = default_for_language(language)  # type: ignore[arg-type]
        return cls(initial_model=model)

    def _ensure_provider(self) -> EmbeddingProvider:
        if self._provider is None:
            caps = detect_hardware()
            device, _ = decide_device(self._model, caps)
            self._provider = TransformersProvider(
                self._model, device=device, fp16=(device == "cuda"),
            )
        if not self._provider.is_loaded:
            try:
                self._provider.load()
            except Exception as e:
                # Detect the broken-transformers / EncoderDecoderCache import
                # chain and surface a distinct exception so callers can
                # decide whether to skip the task or escalate to the user.
                # Anything else just re-raises as before.
                if _looks_like_broken_backend(e):
                    raise EmbeddingBackendUnavailable(
                        "本地 embedding 后端无法加载（sentence-transformers / "
                        "transformers 版本不兼容）：" + str(e)
                    ) from e
                raise
            # OOM fallback warnings only exist after load(); copy them
            # up so the API surface can hand them to the user.
            extra = getattr(self._provider, "load_warnings", None) or []
            if extra:
                with self._warnings_lock:
                    self._pending_warnings.extend(extra)
        return self._provider

    # ─────────── pending warnings ───────────

    def get_and_clear_pending_warnings(self) -> list[str]:
        """Drain warnings accumulated since the last call.

        Mainly used by ``GET /api/embedding/current`` so the UI can show
        an OOM-driven CPU fallback once and not re-show it on every
        poll.
        """
        with self._warnings_lock:
            out = list(self._pending_warnings)
            self._pending_warnings.clear()
        return out

    # ─── cache ───
    def _cache_key(self, text: str) -> str:
        return f"{self._model.model_key}::{self.compute_text_hash(text)}"

    def _cache_get(self, key: str) -> bytes | None:
        with self._cache_lock:
            val = self._cache.get(key)
            if val is None:
                return None
            # Move-to-end for LRU.
            self._cache.move_to_end(key)
            return val

    def _cache_put(self, key: str, value: bytes) -> None:
        with self._cache_lock:
            self._cache[key] = value
            self._cache.move_to_end(key)
            while len(self._cache) > _CACHE_SIZE:
                self._cache.popitem(last=False)

    def _cache_clear(self) -> None:
        with self._cache_lock:
            self._cache.clear()

    # Test helper.
    def _cache_size(self) -> int:
        with self._cache_lock:
            return len(self._cache)


def get_embedding_service() -> EmbeddingService:
    """Process-wide singleton accessor."""
    return EmbeddingService.get()
