"""Embedding-model provider implementations.

A provider owns the heavy lifting — model loading, GPU placement,
batched inference — but knows nothing about caching, hashing or
settings. The ``EmbeddingService`` layer composes one provider at a
time and swaps providers when the user picks a different model.
"""
from .base import EmbeddingProvider
from .transformers import TransformersProvider

__all__ = ["EmbeddingProvider", "TransformersProvider"]
