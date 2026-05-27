"""Provider abstract base class."""
from __future__ import annotations

import abc
from typing import Any


class EmbeddingProvider(abc.ABC):
    """Backend that loads + runs a single embedding model.

    The service layer owns lifecycle (when to ``load`` / ``unload``)
    and caching. Providers only do the heavy lifting.
    """

    @abc.abstractmethod
    def load(self) -> None:
        """Load the model into memory. May download HF weights on first call."""
        ...

    @abc.abstractmethod
    def unload(self) -> None:
        """Release the model. Idempotent."""
        ...

    @abc.abstractmethod
    def embed(self, texts: list[str], normalize: bool = True) -> Any:
        """Embed a batch. Returns numpy float32 array of shape (len(texts), dim)."""
        ...

    @abc.abstractmethod
    def get_device(self) -> str:
        """``'cuda'`` / ``'cpu'`` / ``'mps'`` — actual placement after load()."""
        ...

    @property
    @abc.abstractmethod
    def dimension(self) -> int:
        """The model's output dimension."""
        ...

    @property
    def is_loaded(self) -> bool:
        """True iff ``load()`` has completed successfully."""
        return False
