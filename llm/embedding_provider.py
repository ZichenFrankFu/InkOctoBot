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
        try:
            # huggingface_hub fires a FutureWarning about `resume_download`
            # and transformers emits a "Special tokens have been added"
            # advisory every load — they obscure real errors in the log.
            # Same filter pair as ui/backend/.../providers/transformers.py.
            import warnings as _w
            with _w.catch_warnings():
                _w.filterwarnings(
                    "ignore", category=FutureWarning,
                    message=".*resume_download.*",
                )
                _w.filterwarnings(
                    "ignore", category=UserWarning,
                    message=".*Special tokens have been added.*",
                )
                self._model = SentenceTransformer(self._MODEL_NAME)
        except ImportError as e:
            # Typical env breakage: sentence-transformers >= 3.x needs
            # transformers >= 4.41 (which exports EncoderDecoderCache).
            # Surface the upgrade command so the user can act on it instead
            # of staring at a 500 / "cannot import EncoderDecoderCache".
            raise ImportError(
                f"sentence-transformers 加载失败：{e}。\n"
                "通常是 transformers 版本过旧导致 (sentence-transformers ≥ 3 需要 "
                "transformers ≥ 4.41)。请执行：\n"
                "  pip install -U 'transformers>=4.41' 'sentence-transformers>=2.7'\n"
                "如不便升级，可在 Settings → 模型设置 切到 OpenAI 后端，或让系统自动降级到 TF-IDF。"
            ) from e
        except Exception as e:  # torch / cuda / disk / network errors
            raise RuntimeError(
                f"加载本地 embedding 模型 {self._MODEL_NAME} 失败：{e}"
            ) from e

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


class TfidfFallbackProvider(BaseEmbeddingProvider):
    """Stateless hashing-TF-IDF fallback when sentence-transformers can't load.

    Tokenizes via jieba (whitespace fallback) and projects to a fixed-size
    feature space with ``HashingVectorizer`` so vectors are comparable across
    batches without a fit step. Cosine ≡ dot product after L2 normalization.

    The resulting vectors aren't semantically as good as a real embedding
    model, but they keep similarity search working when the env is broken
    (e.g. sentence-transformers ↔ transformers version mismatch).
    """

    _DIM = 384
    _NAME = "tfidf_jieba_384"

    def __init__(self):
        self._vec = None

    @staticmethod
    def _tokenize(text: str) -> list[str]:
        try:
            import jieba  # noqa: WPS433
            return [t for t in jieba.cut(text or "") if t.strip()]
        except Exception:
            return (text or "").split()

    def _ensure(self) -> None:
        if self._vec is not None:
            return
        try:
            from sklearn.feature_extraction.text import HashingVectorizer
        except ImportError as e:
            raise ImportError(
                "TF-IDF 兜底需要 scikit-learn：pip install scikit-learn"
            ) from e
        self._vec = HashingVectorizer(
            n_features=self._DIM,
            analyzer=self._tokenize,
            norm=None,                # we normalize ourselves below
            alternate_sign=False,     # non-negative features → meaningful cosine
        )

    async def embed(self, texts: list[str]) -> Any:
        import numpy as np
        if not texts:
            return np.zeros((0, self._DIM), dtype="float32")
        self._ensure()
        # `transform` is fast (no fit), pure-Python tokenization happens
        # inside; run in a thread to avoid blocking the event loop on large
        # batches.
        import asyncio
        loop = asyncio.get_event_loop()
        sparse = await loop.run_in_executor(
            None, lambda: self._vec.transform(texts),
        )
        arr = sparse.toarray().astype("float32", copy=False)
        norms = np.linalg.norm(arr, axis=1, keepdims=True)
        # Empty-text rows hash to zero vectors — replace with a tiny
        # constant so cosine doesn't divide-by-zero AND the row is still
        # distinct from real content.
        norms[norms == 0] = 1.0
        return arr / norms

    @property
    def dim(self) -> int:
        return self._DIM

    @property
    def name(self) -> str:
        return f"local:{self._NAME}"


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


class _LocalWithFallback(BaseEmbeddingProvider):
    """Try ``LocalSTProvider`` first; on probe failure (broken env, version
    mismatch, no GPU + slow CPU OOM, …) commit to ``TfidfFallbackProvider``
    so the indexer keeps working instead of 500-ing.

    The probe runs at first access to ``.name`` / ``.dim`` / ``.embed()`` so
    the collection name is consistent with the actual backend used —
    preventing 384-dim ST vectors and 384-dim TF-IDF vectors from landing in
    the same ChromaDB collection (the similarity geometry is incompatible).

    The probe is fast in the broken-env case (the EncoderDecoderCache
    ImportError fires during class loading, before any model download), and
    slow but successful otherwise — same cost as the first real embed call.
    """

    def __init__(self) -> None:
        self._impl: BaseEmbeddingProvider = LocalSTProvider()
        self._committed = False
        self._fallback_reason: str | None = None

    def _commit(self) -> None:
        """Resolve which backend to use. Cheap on subsequent calls."""
        if self._committed:
            return
        if isinstance(self._impl, LocalSTProvider):
            try:
                self._impl._ensure()
            except (ImportError, RuntimeError) as e:
                self._fallback_reason = str(e)
                logger.warning(
                    "本地 embedding 不可用，自动降级到 TF-IDF：%s\n"
                    "请尽快修复 sentence-transformers 环境或切到 OpenAI 后端以恢复语义检索质量。",
                    e,
                )
                self._impl = TfidfFallbackProvider()
        self._committed = True

    async def embed(self, texts: list[str]) -> Any:
        self._commit()
        return await self._impl.embed(texts)

    @property
    def dim(self) -> int:
        self._commit()
        return self._impl.dim

    @property
    def name(self) -> str:
        self._commit()
        return self._impl.name


# Module-level cache so make_indexer's per-request call doesn't re-load the
# sentence-transformers model (or re-run the fallback probe). Keyed by the
# backend choice so switching settings via /settings reloads cleanly via
# `clear_embedding_provider_cache()`.
_PROVIDER_CACHE: dict[str, BaseEmbeddingProvider] = {}


def clear_embedding_provider_cache() -> None:
    """Drop the cached providers — call after Settings → embedding_backend
    changes so the next request picks up the new choice."""
    _PROVIDER_CACHE.clear()


def get_embedding_provider(backend: str | None = None) -> BaseEmbeddingProvider:
    """Resolve the embedding backend from settings.json (or an explicit
    override). Reads OpenAI api_key from settings.providers.openai when
    backend="openai".

    ``backend="local"`` (the default) returns a wrapper that probes
    sentence-transformers on first access and auto-falls-back to TF-IDF if
    loading fails (env issue, version mismatch, …) so callers don't see 500s.
    Pass ``backend="local_strict"`` to disable the fallback for diagnostics.
    Pass ``backend="tfidf"`` to force the TF-IDF path."""
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
    cached = _PROVIDER_CACHE.get(chosen)
    if cached is not None:
        return cached
    if chosen == "openai":
        prov_cfg = (data.get("providers") or {}).get("openai") or {}
        provider: BaseEmbeddingProvider = OpenAIEmbeddingProvider(
            api_key=prov_cfg.get("api_key") or None,
            base_url=prov_cfg.get("base_url") or None,
        )
    elif chosen == "tfidf":
        provider = TfidfFallbackProvider()
    elif chosen == "local_strict":
        provider = LocalSTProvider()
    else:
        # default: local with TF-IDF fallback so a broken sentence-transformers
        # env doesn't take down indexing entirely.
        provider = _LocalWithFallback()
    _PROVIDER_CACHE[chosen] = provider
    return provider


def collection_name_for(backend_name: str) -> str:
    """Stable per-backend ChromaDB collection name so switching backends
    doesn't mix vectors of different dimensions."""
    # Strip leading "local:" or "openai:" for readability but keep the
    # model-name to disambiguate.
    short = backend_name.split(":", 1)[-1] if ":" in backend_name else backend_name
    short = short.replace("/", "_").replace(".", "_")
    return f"reference_works__{short}"
