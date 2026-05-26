"""
Constraint Violation Detector — semantic-level constraint checking.

README §2.5: Uses ChromaDB embedding similarity to detect when generated
text violates constraint rules.  More reliable than token-level banning
for Chinese text.
"""
from __future__ import annotations

import logging
from typing import Any

from knowledge.vector_store import VectorStore

logger = logging.getLogger("inkoctobot.agents.guardrails.violation_detector")


class ViolationDetector:
    """Semantic constraint violation detection using vector similarity."""

    def __init__(self, persist_dir: str | None = None):
        self._store = VectorStore(
            persist_dir=persist_dir, collection_name="constraint_rules",
        )
        self._threshold = 0.35  # cosine distance threshold (lower = more similar)

    def index_constraint(self, rule_id: str, description: str,
                          bad_example: str = "", metadata: dict[str, Any] | None = None) -> None:
        """Index a constraint rule and its bad example for semantic detection."""
        meta = metadata or {}
        meta["rule_id"] = rule_id
        self._store.add(f"constraint_{rule_id}", description, meta)
        if bad_example:
            meta_bad = {**meta, "is_bad_example": True}
            self._store.add(f"bad_{rule_id}", bad_example, meta_bad)

    def check_text(
        self,
        text: str,
        *,
        chunk_size: int = 200,
        project_id: str = "",
    ) -> list[dict[str, Any]]:
        """Check text for potential constraint violations."""
        violations: list[dict[str, Any]] = []
        chunks = self._chunk_text(text, chunk_size)

        for i, chunk in enumerate(chunks):
            results = self._store.query(chunk, n_results=3)
            for result in results:
                dist = result.get("distance", 1.0)
                if dist < self._threshold:
                    violations.append({
                        "chunk_index": i,
                        "chunk_text": chunk[:100],
                        "matched_rule": result.get("text", ""),
                        "rule_id": result.get("metadata", {}).get("rule_id", ""),
                        "distance": round(dist, 4),
                        "is_bad_example": result.get("metadata", {}).get("is_bad_example", False),
                    })

        return violations

    @staticmethod
    def _chunk_text(text: str, size: int) -> list[str]:
        """Split text into overlapping chunks for scanning."""
        chunks = []
        step = size // 2
        for i in range(0, len(text), step):
            chunk = text[i:i + size]
            if chunk.strip():
                chunks.append(chunk)
        return chunks
