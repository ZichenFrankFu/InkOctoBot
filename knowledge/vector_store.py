"""
ChromaDB unified wrapper.

Provides a simplified interface over ChromaDB for embedding-based
semantic search used by Layer 3, constraint store, and reference DB.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger("inkoctobot.knowledge.vector_store")

_DEFAULT_DIR = Path(__file__).resolve().parent.parent / "data" / "chromadb"


class VectorStore:
    """Thin wrapper around ChromaDB."""

    def __init__(self, persist_dir: str | Path | None = None, collection_name: str = "default"):
        self._persist_dir = str(persist_dir or _DEFAULT_DIR)
        self._collection_name = collection_name
        self._client = None
        self._collection = None

    def _ensure_client(self) -> None:
        if self._client is not None:
            return
        try:
            import chromadb
            from chromadb.config import Settings
        except ImportError:
            raise ImportError("pip install chromadb")
        Path(self._persist_dir).mkdir(parents=True, exist_ok=True)
        self._client = chromadb.PersistentClient(
            path=self._persist_dir,
            settings=Settings(anonymized_telemetry=False),
        )
        self._collection = self._client.get_or_create_collection(
            name=self._collection_name,
            metadata={"hnsw:space": "cosine"},
        )
        logger.info("ChromaDB collection '%s' ready (%d docs)",
                     self._collection_name, self._collection.count())

    @property
    def collection(self) -> Any:
        self._ensure_client()
        return self._collection

    def add(
        self,
        doc_id: str,
        text: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self._ensure_client()
        self._collection.upsert(
            ids=[doc_id],
            documents=[text],
            metadatas=[metadata or {}],
        )

    def add_batch(
        self,
        ids: list[str],
        texts: list[str],
        metadatas: list[dict[str, Any]] | None = None,
    ) -> None:
        self._ensure_client()
        self._collection.upsert(
            ids=ids,
            documents=texts,
            metadatas=metadatas or [{} for _ in ids],
        )

    def add_batch_with_embeddings(
        self,
        ids: list[str],
        texts: list[str],
        embeddings: list[list[float]] | Any,  # numpy.ndarray (N, dim) accepted
        metadatas: list[dict[str, Any]] | None = None,
    ) -> None:
        """Upsert with PRE-COMPUTED embeddings — bypasses ChromaDB's
        default embedding function. Used by reference-works indexing
        where we want explicit control over the model."""
        self._ensure_client()
        # ChromaDB tolerates either list[list[float]] or np.ndarray; if
        # the caller passed a numpy array, convert to list of lists so
        # the downstream client doesn't fight numpy's iteration semantics
        # on some versions.
        try:
            import numpy as np  # type: ignore
            if isinstance(embeddings, np.ndarray):
                emb_list = embeddings.tolist()
            else:
                emb_list = list(embeddings)
        except ImportError:
            emb_list = list(embeddings)
        self._collection.upsert(
            ids=ids,
            documents=texts,
            embeddings=emb_list,
            metadatas=metadatas or [{} for _ in ids],
        )

    def query_with_embedding(
        self,
        embedding: list[float] | Any,
        n_results: int = 5,
        where: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Query by a pre-computed query embedding (mirror of `query`
        for use with the explicit-backend indexer)."""
        self._ensure_client()
        try:
            import numpy as np
            if hasattr(embedding, "tolist"):
                emb = embedding.tolist()
            else:
                emb = list(embedding)
        except ImportError:
            emb = list(embedding)
        kw: dict[str, Any] = {"query_embeddings": [emb], "n_results": n_results}
        if where:
            kw["where"] = where
        results = self._collection.query(**kw)
        out: list[dict[str, Any]] = []
        ids = results.get("ids", [[]])[0]
        docs = results.get("documents", [[]])[0]
        metas = results.get("metadatas", [[]])[0]
        dists = results.get("distances", [[]])[0]
        for i in range(len(ids)):
            out.append({
                "id": ids[i],
                "text": docs[i] if docs else "",
                "metadata": metas[i] if metas else {},
                "distance": dists[i] if dists else 0.0,
            })
        return out

    def get_many(self, ids: list[str]) -> set[str]:
        """Return the subset of ids that already exist in the collection.
        Used by the indexer to skip already-embedded chunks."""
        self._ensure_client()
        if not ids:
            return set()
        try:
            result = self._collection.get(ids=ids)
            return set(result.get("ids") or [])
        except Exception:
            return set()

    def query(
        self,
        query_text: str,
        n_results: int = 5,
        where: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Query by semantic similarity. Returns list of {id, text, metadata, distance}."""
        self._ensure_client()
        kw: dict[str, Any] = {"query_texts": [query_text], "n_results": n_results}
        if where:
            kw["where"] = where
        results = self._collection.query(**kw)
        out: list[dict[str, Any]] = []
        ids = results.get("ids", [[]])[0]
        docs = results.get("documents", [[]])[0]
        metas = results.get("metadatas", [[]])[0]
        dists = results.get("distances", [[]])[0]
        for i in range(len(ids)):
            out.append({
                "id": ids[i],
                "text": docs[i] if docs else "",
                "metadata": metas[i] if metas else {},
                "distance": dists[i] if dists else 0.0,
            })
        return out

    def get(self, doc_id: str) -> dict[str, Any] | None:
        self._ensure_client()
        result = self._collection.get(ids=[doc_id])
        if not result["ids"]:
            return None
        return {
            "id": result["ids"][0],
            "text": result["documents"][0] if result.get("documents") else "",
            "metadata": result["metadatas"][0] if result.get("metadatas") else {},
        }

    def delete(self, doc_id: str) -> None:
        self._ensure_client()
        self._collection.delete(ids=[doc_id])

    def delete_where(self, where: dict[str, Any]) -> None:
        self._ensure_client()
        results = self._collection.get(where=where)
        if results["ids"]:
            self._collection.delete(ids=results["ids"])

    def count(self) -> int:
        self._ensure_client()
        return self._collection.count()
