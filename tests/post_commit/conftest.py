"""Shared fixtures for post_commit tests.

Auto-mocks every sub-task's outbound LLM / heavy-IO call so framework
tests don't accidentally try to reach a real provider, and so each
test's own mock.patch can override specific behaviors. Without this,
pipeline tests would hang trying to talk to a real Ollama endpoint
that isn't running in the sandbox.
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from ui.backend.app.services.commit_pipeline.sub_tasks import (
    chromadb_indexer, event_extractor, state_extractor, summarizer,
)


@pytest.fixture(autouse=True)
def _stub_subtask_llm_io(monkeypatch):
    """Replace every sub-task's outbound dependency with a no-op stub.

    Tests that need specific LLM behaviors patch over these defaults
    via their own mock.patch.object — autouse here just guarantees
    framework tests don't hit a real network.
    """

    async def _summary_stub(chapter_text, chapter_num, **_kw):
        return ("默认标题", "默认摘要内容", "mock/m")

    async def _events_stub(chapter_text, chapter_num, characters, **_kw):
        return ([], "mock/m")

    async def _state_stub(chapter_text, chapter_num, existing_entities, **_kw):
        return ({"state_deltas": [], "ledger_deltas": [],
                  "emotion_deltas": [], "new_entities": []}, "mock/m")

    monkeypatch.setattr(summarizer, "_call_llm", _summary_stub)
    monkeypatch.setattr(event_extractor, "_call_llm", _events_stub)
    monkeypatch.setattr(state_extractor, "_call_llm", _state_stub)

    # chromadb_indexer reaches for the EmbeddingService + VectorStore.
    # Replace both with no-ops so the framework tests don't pull torch
    # or chromadb into the test runner.
    class _FakeModel:
        model_key = "mock-model"
        dimension = 16

    class _FakeSvc:
        def get_current_model(self):
            return _FakeModel()
        def embed_batch(self, texts):
            return [np.ones(16, dtype="float32").tobytes() for _ in texts]

    monkeypatch.setattr(
        "ui.backend.app.services.embedding.get_embedding_service",
        lambda: _FakeSvc(),
    )

    class _FakeStore:
        def __init__(self, persist_dir=None, collection_name="default"):
            self.collection_name = collection_name
        def add_batch_with_embeddings(self, **kwargs):
            pass

    monkeypatch.setattr("knowledge.vector_store.VectorStore", _FakeStore)
    yield
