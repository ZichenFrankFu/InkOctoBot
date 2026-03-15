"""
Layer 3: Semantic Memory (ChromaDB).

Stores all settings, character states, events, world book entries,
and constraint rules as vector embeddings for semantic retrieval.
"""
from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from rag.vector_store import VectorStore

logger = logging.getLogger("inkoctobot.rag.memory.semantic_store")


class SemanticMemory:
    """
    Layer 3 — vector-indexed long-term memory.

    Each memory entry is embedded and stored in ChromaDB.  Agents can
    query by natural language to find relevant context.
    """

    def __init__(self, persist_dir: str | None = None, collection_name: str = "semantic_memory"):
        self._store = VectorStore(persist_dir=persist_dir, collection_name=collection_name)

    def store(
        self,
        project_id: str,
        content: str,
        *,
        memory_type: str = "general",
        chapter_num: int = 0,
        characters: list[str] | None = None,
        tags: list[str] | None = None,
        source: str = "",
    ) -> str:
        """Store a memory entry with metadata for filtered retrieval."""
        doc_id = f"mem_{uuid.uuid4().hex[:12]}"
        metadata: dict[str, Any] = {
            "project_id": project_id,
            "memory_type": memory_type,
            "chapter_num": chapter_num,
            "characters": json.dumps(characters or [], ensure_ascii=False),
            "tags": json.dumps(tags or [], ensure_ascii=False),
            "source": source,
        }
        self._store.add(doc_id, content, metadata)
        return doc_id

    def query(
        self,
        query_text: str,
        project_id: str,
        *,
        n_results: int = 5,
        memory_type: str | None = None,
    ) -> list[dict[str, Any]]:
        """Semantic search within a project's memory."""
        where: dict[str, Any] = {"project_id": project_id}
        if memory_type:
            where["memory_type"] = memory_type
        return self._store.query(query_text, n_results=n_results, where=where)

    def query_for_character(
        self,
        query_text: str,
        project_id: str,
        character_name: str,
        n_results: int = 5,
    ) -> list[dict[str, Any]]:
        """Search memory filtered by character involvement."""
        # Try to use ChromaDB $contains filter for character name first
        results = self._store.query(
            query_text, n_results=n_results * 2,
            where={"project_id": project_id},
        )
        filtered = []
        for r in results:
            chars_raw = r.get("metadata", {}).get("characters", "[]")
            # Fast string check before full JSON parse
            if chars_raw == "[]" or character_name in chars_raw:
                if chars_raw == "[]":
                    filtered.append(r)
                else:
                    chars = json.loads(chars_raw)
                    if not chars or character_name in chars:
                        filtered.append(r)
                if len(filtered) >= n_results:
                    break
        return filtered

    def store_permanent_fact(self, project_id: str, content: str,
                             chapter_num: int, characters: list[str] | None = None) -> str:
        return self.store(
            project_id, content,
            memory_type="permanent_fact",
            chapter_num=chapter_num,
            characters=characters,
            source="consolidator",
        )

    def store_setting(self, project_id: str, content: str, setting_type: str = "world") -> str:
        return self.store(
            project_id, content,
            memory_type="setting",
            tags=[setting_type],
            source="world_book",
        )

    def store_character_state(self, project_id: str, character_name: str,
                               state_desc: str, chapter_num: int) -> str:
        return self.store(
            project_id, f"{character_name}: {state_desc}",
            memory_type="character_state",
            chapter_num=chapter_num,
            characters=[character_name],
            source="consolidator",
        )

    def delete_project(self, project_id: str) -> None:
        self._store.delete_where({"project_id": project_id})

    def count(self) -> int:
        return self._store.count()
