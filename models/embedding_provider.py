"""
Pluggable embedding provider for reference-works similarity search.

Two backends, selected via `settings.embedding_backend`:

- ``local``: sentence-transformers ``shibing624/text2vec-base-chinese``
  (384-dim, offline, optimized for Chinese). Lazy-loaded — the model
  weights only download on first use.
- ``openai``: ``text-embedding-3-small`` (1536-dim) via the OpenAI SDK,
  re-using the api_key configured in settings.json.

A separate ``async def embed(texts) -> np.ndarray`` method on each
provider keeps the call sites uniform. Callers should NOT pre-import
heavy deps; the provider does it on first ``embed()`` so a missing
``sentence-transformers`` only breaks the local path.
"""
from __future__ import annotations

import abc
import logging
from typing import Any, Iterable

logger = logging.getLogger("inkoctobot.models.embedding")


class BaseEmbeddingProvider(abc.ABC):
    """Embed a batch of strings to a (N, dim) float32 numpy array."""

    @abc.abstractmethod
    async def embed(self, texts: list[str]) -> Any:
        """Returns numpy.ndarray of shape (len(texts), self.dim)."""
        ...

    @property
    @abc.abstractmethod
    def dim(self) -> int: ...

    @property
    @abc.abstractmethod
    def name(self) -> str: ...

    @property
    def collection_suffix(self) -> str:
        """A stable string that identifies this backend in the collection
        name so switching backends doesn't conflate vectors from
        different models."""
        return self.name.replace("/", "_").replace(":", "_")


class LocalSTProvider(BaseEmbeddingProvider):
    """sentence-transformers/text2vec-base-chinese — 384-dim, Chinese-optimized.

    The model is lazy-loaded on the first ``embed()`` call so importing
    this module doesn't pull in torch/transformers."""

    _MODEL_NAME = "shibing624/text2vec-base-chinese"
    _DIM = 384

    def __init__(self):
        self._model = None

    def _ensure(self) -> None:
        if self._model is not None:
            return
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as e:
            raise ImportError(
                "本地 embedding 后端需要 sentence-transformers："
                "pip install sentence-transformers torch"
            ) from e
        logger.info("Loading sentence-transformers model %s ...", self._MODEL_NAME)
        self._model = SentenceTransformer(self._MODEL_NAME)

    async def embed(self, texts: list[str]) -> Any:
        if not texts:
            import numpy as np
            return np.zeros((0, self._DIM), dtype="float32")
        # SentenceTransformer.encode is CPU-bound; run in a thread so
        # the event loop isn't blocked during long batches.
        import asyncio
        self._ensure()
        loop = asyncio.get_event_loop()
        arr = await loop.run_in_executor(
            None,
            lambda: self._model.encode(
                texts, normalize_embeddings=True,
                show_progress_bar=False, convert_to_numpy=True,
            ),
        )
        return arr.astype("float32", copy=False)

    @property
    def dim(self) -> int:
        return self._DIM

    @property
    def name(self) -> str:
        return f"local:{self._MODEL_NAME}"


class OpenAIEmbeddingProvider(BaseEmbeddingProvider):
    """OpenAI text-embedding-3-small — 1536-dim, hosted.

    Reuses the api_key configured under ``settings.providers.openai`` in
    settings.json so the user doesn't need to enter it twice."""

    _MODEL_NAME = "text-embedding-3-small"
    _DIM = 1536

    def __init__(self, api_key: str | None = None, base_url: str | None = None):
        self._api_key = api_key
        self._base_url = base_url
        self._client = None

    def _ensure(self) -> None:
        if self._client is not None:
            return
        try:
            from openai import AsyncOpenAI
        except ImportError as e:
            raise ImportError("pip install openai") from e
        kw: dict[str, Any] = {}
        if self._api_key:
            kw["api_key"] = self._api_key
        if self._base_url:
            kw["base_url"] = self._base_url
        self._client = AsyncOpenAI(**kw)

    async def embed(self, texts: list[str]) -> Any:
        import numpy as np
        if not texts:
            return np.zeros((0, self._DIM), dtype="float32")
        self._ensure()
        # OpenAI accepts up to 2048 inputs per call but we batch smaller
        # for retry granularity.
        BATCH = 128
        out: list[list[float]] = []
        for i in range(0, len(texts), BATCH):
            chunk = texts[i:i + BATCH]
            resp = await self._client.embeddings.create(
                model=self._MODEL_NAME, input=chunk,
            )
            for item in resp.data:
                out.append(item.embedding)
        return np.array(out, dtype="float32")

    @property
    def dim(self) -> int:
        return self._DIM

    @property
    def name(self) -> str:
        return f"openai:{self._MODEL_NAME}"


# ── Factory ─────────────────────────────────────────────────────────


def get_embedding_provider(backend: str | None = None) -> BaseEmbeddingProvider:
    """Resolve the embedding backend from settings.json (or an explicit
    override). Reads OpenAI api_key from settings.providers.openai when
    backend="openai"."""
    import json
    from pathlib import Path

    # Lazy import to avoid pulling FastAPI when used from CLI scripts
    try:
        from ui.backend.app.settings import settings as _app_settings
        settings_path = _app_settings.get_data_path("settings.json")
    except Exception:
        settings_path = Path.cwd() / "data" / "settings.json"

    data: dict = {}
    if settings_path.exists():
        try:
            data = json.loads(settings_path.read_text("utf-8"))
        except Exception as e:
            logger.warning("Failed to read settings.json: %s", e)

    chosen = (backend or data.get("embedding_backend") or "local").lower()
    if chosen == "openai":
        prov_cfg = (data.get("providers") or {}).get("openai") or {}
        return OpenAIEmbeddingProvider(
            api_key=prov_cfg.get("api_key") or None,
            base_url=prov_cfg.get("base_url") or None,
        )
    # default: local
    return LocalSTProvider()


def collection_name_for(backend_name: str) -> str:
    """Stable per-backend ChromaDB collection name so switching backends
    doesn't mix vectors of different dimensions."""
    # Strip leading "local:" or "openai:" for readability but keep the
    # model-name to disambiguate.
    short = backend_name.split(":", 1)[-1] if ":" in backend_name else backend_name
    short = short.replace("/", "_").replace(".", "_")
    return f"reference_works__{short}"
