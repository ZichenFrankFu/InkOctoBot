"""sentence-transformers / Hugging Face transformers backend.

Handles all seven registered models with a uniform shape:
- Load via ``SentenceTransformer(model.hf_repo, device=device,
  model_kwargs={"torch_dtype": fp16/fp32})``.
- Embed batches with ``normalize_embeddings=True`` so cosine ≡ dot.
- On OOM during load, downgrade to CPU and retry once.

Lazy-loaded: importing this module doesn't pull torch. ``load()`` is
when the heavy machinery comes online.
"""
from __future__ import annotations

import gc
import logging
from typing import Any

from ..registry import ModelInfo
from .base import EmbeddingProvider

logger = logging.getLogger("inkoctobot.services.embedding.providers.transformers")


class TransformersProvider(EmbeddingProvider):
    """Wraps ``SentenceTransformer`` over the registered model.

    ``device`` is the *requested* placement; ``get_device()`` returns
    the *actual* placement after ``load()`` (so OOM-driven CPU
    downgrade is observable).
    """

    def __init__(self, model: ModelInfo, device: str = "cpu", *, fp16: bool = False) -> None:
        self._model = model
        self._requested_device = device
        self._actual_device = device  # updated by load()
        self._fp16 = fp16
        self._st = None  # type: ignore[var-annotated]
        self._load_warnings: list[str] = []

    # ─────────── lifecycle ───────────

    def load(self) -> None:
        if self._st is not None:
            return
        self._st = self._load_with_fallback(self._requested_device)

    def unload(self) -> None:
        if self._st is None:
            return
        self._st = None
        gc.collect()
        try:
            import torch  # noqa: WPS433
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:
            pass

    @property
    def is_loaded(self) -> bool:
        return self._st is not None

    # ─────────── inference ───────────

    def embed(self, texts: list[str], normalize: bool = True) -> Any:
        if self._st is None:
            self.load()
        if not texts:
            import numpy as np
            return np.zeros((0, self._model.dimension), dtype="float32")
        arr = self._st.encode(  # type: ignore[union-attr]
            texts,
            normalize_embeddings=normalize,
            show_progress_bar=False,
            convert_to_numpy=True,
        )
        arr = arr.astype("float32", copy=False)
        # Spec § 11.4: NaN / all-zero vectors are pathology, not data.
        # Raise so the caller's per-row error handler isolates the
        # offending text instead of persisting a corrupt vector.
        import numpy as np
        if np.isnan(arr).any():
            raise RuntimeError(
                f"embedding produced NaN for {self._model.model_key}"
            )
        if arr.ndim == 2:
            zero_rows = np.where(~arr.any(axis=1))[0]
            if zero_rows.size:
                raise RuntimeError(
                    f"embedding produced all-zero vector(s) for "
                    f"{self._model.model_key} at row indices "
                    f"{zero_rows.tolist()}"
                )
        return arr

    def get_device(self) -> str:
        return self._actual_device

    @property
    def dimension(self) -> int:
        return self._model.dimension

    @property
    def load_warnings(self) -> list[str]:
        """Warnings collected during ``load()`` — e.g. OOM downgrade."""
        return list(self._load_warnings)

    # ─────────── helpers ───────────

    def _load_with_fallback(self, device: str):
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as e:
            raise ImportError(
                "embedding requires sentence-transformers: "
                "pip install sentence-transformers"
            ) from e

        try:
            return self._do_load(SentenceTransformer, device)
        except Exception as e:
            err_text = str(e).lower()
            if device == "cuda" and (
                "out of memory" in err_text or "oom" in err_text
                or "cuda" in err_text or "device" in err_text
            ):
                logger.warning(
                    "GPU load failed for %s (%s); falling back to CPU.",
                    self._model.model_key, e,
                )
                self._load_warnings.append(
                    f"GPU 加载 {self._model.display_name} 失败（{e}）；已降级到 CPU。"
                )
                return self._do_load(SentenceTransformer, "cpu")
            raise

    def _do_load(self, SentenceTransformer, device: str):
        model_kwargs: dict[str, Any] = {}
        if device == "cuda" and self._fp16:
            try:
                import torch  # noqa: WPS433
                model_kwargs["torch_dtype"] = torch.float16
            except Exception:
                pass
        logger.info(
            "loading %s on %s (fp16=%s)",
            self._model.hf_repo, device, self._fp16 and device == "cuda",
        )
        # Quiet the well-known noisy deprecation warnings that fire during
        # model load: huggingface_hub's resume_download FutureWarning and
        # transformers' "Special tokens have been added in the vocabulary"
        # advice. They surface every time the model is loaded and obscure
        # real errors in the log.
        import warnings as _warnings
        with _warnings.catch_warnings():
            _warnings.filterwarnings(
                "ignore", category=FutureWarning,
                message=".*resume_download.*",
            )
            _warnings.filterwarnings(
                "ignore", category=UserWarning,
                message=".*Special tokens have been added.*",
            )
            st = SentenceTransformer(
                self._model.hf_repo,
                device=device,
                model_kwargs=model_kwargs or None,
            )
        self._actual_device = device
        return st
