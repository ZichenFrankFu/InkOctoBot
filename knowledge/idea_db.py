"""IdeaDB — the 灵感库 (inspiration library) data layer.

Free-text idea snippets that the 灵感搜索 page can store and
similarity-search. Lives in its own SQLite file (``data/idea.db``) so
the reference-works + inspirations surfaces stay decoupled.

LOADER_SPEC Loader 4 (Batch 3) extends this layer with:
- ``project_id``-scoped queries (NULL rows are "global" and visible to
  every project).
- ``tags`` list alongside the legacy ``category`` string.
- per-row ``embedding`` + ``embedding_text_hash`` for outline-based
  similarity ranking, recomputed on content change.
- ``used_in_chapters`` list for soft "don't reuse the same beat twice
  in a row" filtering.
"""
from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

logger = logging.getLogger("inkoctobot.knowledge.idea_db")


def _gid(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


@contextmanager
def _conn(db_path: str) -> Iterator[sqlite3.Connection]:
    c = sqlite3.connect(str(db_path))
    c.row_factory = sqlite3.Row
    try:
        yield c
    finally:
        c.close()


def inspiration_embedding_text(entry: dict) -> str:
    """Canonical text used to compute an inspiration's embedding.

    Title + tags + content concatenated so the embedding reflects the
    label-as-context plus the body. Single source of truth for both
    the upsert path (hashing) and the loader path (stale-check).
    """
    title = (entry.get("title") or "").strip()
    content = (entry.get("content") or "").strip()
    tags = entry.get("tags") or []
    if isinstance(tags, str):
        try:
            tags = json.loads(tags)
        except (TypeError, json.JSONDecodeError):
            tags = []
    tags_str = "、".join(str(t).strip() for t in tags if str(t or "").strip())
    parts = [title, tags_str, content]
    return "\n".join(p for p in parts if p)


def hash_embedding_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _row_to_payload(row: sqlite3.Row, *, include_embedding: bool = False) -> dict:
    r = dict(row)
    out: dict[str, Any] = {
        "id":         r["id"],
        "project_id": r.get("project_id"),
        "category":   r.get("category") or "other",
        "title":      r.get("title") or "",
        "content":    r.get("content") or "",
        "created_at": r.get("created_at"),
        "updated_at": r.get("updated_at"),
    }
    try:
        out["tags"] = json.loads(r.get("tags_json") or "[]")
    except (TypeError, json.JSONDecodeError):
        out["tags"] = []
    try:
        out["used_in_chapters"] = json.loads(r.get("used_in_chapters_json") or "[]")
    except (TypeError, json.JSONDecodeError):
        out["used_in_chapters"] = []
    if include_embedding:
        try:
            out["embedding"] = json.loads(r.get("embedding_json") or "[]")
        except (TypeError, json.JSONDecodeError):
            out["embedding"] = []
        out["embedding_text_hash"] = r.get("embedding_text_hash") or ""
    return out


class IdeaDB:
    """灵感库 data access layer."""

    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        from storage.idea_schema import ensure_idea_tables
        with _conn(self.db_path) as conn:
            ensure_idea_tables(conn)
        logger.info("idea_db opened path=%s", self.db_path)

    # ─────────── CRUD ───────────

    def create_inspiration(
        self,
        category: str,
        title: str,
        content: str,
        *,
        project_id: str | None = None,
        tags: list[str] | None = None,
    ) -> dict:
        iid = _gid("insp")
        tags_json = json.dumps(list(tags or []), ensure_ascii=False)
        new_text = inspiration_embedding_text({
            "title": title, "content": content, "tags": tags or [],
        })
        text_hash = hash_embedding_text(new_text)
        with _conn(self.db_path) as c:
            c.execute(
                "INSERT INTO inspirations (id, project_id, category, title, content, "
                "tags_json, embedding_text_hash) VALUES (?,?,?,?,?,?,?)",
                (iid, project_id,
                 (category or "other").strip() or "other",
                 (title or "").strip(),
                 (content or ""),
                 tags_json,
                 text_hash),
            )
            c.commit()
        logger.info("idea_db create id=%s project=%s category=%s",
                    iid, project_id, category or "other")
        return self.get_inspiration(iid)  # type: ignore[return-value]

    def get_inspiration(
        self, insp_id: str, *, include_embedding: bool = False,
    ) -> dict | None:
        with _conn(self.db_path) as c:
            r = c.execute(
                "SELECT * FROM inspirations WHERE id=?", (insp_id,),
            ).fetchone()
        return _row_to_payload(r, include_embedding=include_embedding) if r else None

    def list_inspirations(
        self,
        *,
        project_id: str | None = None,
        include_embedding: bool = False,
    ) -> list[dict]:
        """Return inspirations newest-updated first.

        When ``project_id`` is provided, returns rows for that project
        plus the global (NULL ``project_id``) rows. Without a project
        filter, returns everything (back-compat for the legacy UI).
        """
        sql = "SELECT * FROM inspirations"
        params: tuple = ()
        if project_id is not None:
            sql += " WHERE project_id IS NULL OR project_id = ?"
            params = (project_id,)
        sql += " ORDER BY updated_at DESC"
        with _conn(self.db_path) as c:
            rows = c.execute(sql, params).fetchall()
        return [_row_to_payload(r, include_embedding=include_embedding) for r in rows]

    def update_inspiration(self, insp_id: str, **fields: Any) -> dict | None:
        """Update fields. Recomputes ``embedding_text_hash`` if title,
        content or tags changed, and clears the stored embedding so the
        loader recomputes it on next read.
        """
        allowed = {"category", "title", "content", "project_id", "tags"}
        sets = ["updated_at=CURRENT_TIMESTAMP"]
        params: list[Any] = []
        normalized: dict[str, Any] = {}
        for k, v in fields.items():
            if k not in allowed or v is None:
                continue
            if k == "tags":
                v = json.dumps(list(v or []), ensure_ascii=False)
                sets.append("tags_json=?")
                normalized["tags"] = v
            else:
                sets.append(f"{k}=?")
                normalized[k] = v
            params.append(v)
        if len(sets) == 1:
            return self.get_inspiration(insp_id)

        # Recompute embedding hash if the canonical text changed.
        cur = self.get_inspiration(insp_id, include_embedding=True)
        if cur is None:
            return None
        new_title   = normalized.get("title",   cur["title"])
        new_content = normalized.get("content", cur["content"])
        new_tags = (
            json.loads(normalized["tags"]) if "tags" in normalized
            else cur.get("tags") or []
        )
        new_text = inspiration_embedding_text({
            "title": new_title, "content": new_content, "tags": new_tags,
        })
        new_hash = hash_embedding_text(new_text)
        if new_hash != cur.get("embedding_text_hash"):
            sets.append("embedding_json=?")
            params.append("[]")
            sets.append("embedding_text_hash=?")
            params.append(new_hash)

        params.append(insp_id)
        with _conn(self.db_path) as c:
            c.execute(
                f"UPDATE inspirations SET {', '.join(sets)} WHERE id=?",
                params,
            )
            c.commit()
        logger.info("idea_db update id=%s fields=%s", insp_id, list(fields.keys()))
        return self.get_inspiration(insp_id)

    def delete_inspiration(self, insp_id: str) -> bool:
        with _conn(self.db_path) as c:
            cur = c.execute("DELETE FROM inspirations WHERE id=?", (insp_id,))
            c.commit()
        ok = cur.rowcount > 0
        logger.info("idea_db delete id=%s ok=%s", insp_id, ok)
        return ok

    # ─────────── embedding + usage tracking ───────────

    def set_embedding(
        self, insp_id: str, embedding: list[float], text_hash: str,
    ) -> None:
        """Persist a freshly-computed embedding."""
        with _conn(self.db_path) as c:
            c.execute(
                "UPDATE inspirations SET embedding_json=?, embedding_text_hash=? "
                "WHERE id=?",
                (json.dumps(list(embedding), ensure_ascii=False), text_hash, insp_id),
            )
            c.commit()

    def mark_used_in_chapter(self, insp_id: str, chapter_num: int) -> None:
        """Append ``chapter_num`` to the used-in-chapters list. Duplicates
        are intentional — the loader uses repetition count to decide
        which inspirations have been over-mined.
        """
        cur = self.get_inspiration(insp_id)
        if cur is None:
            return
        used = list(cur.get("used_in_chapters") or [])
        used.append(int(chapter_num))
        with _conn(self.db_path) as c:
            c.execute(
                "UPDATE inspirations SET used_in_chapters_json=? WHERE id=?",
                (json.dumps(used, ensure_ascii=False), insp_id),
            )
            c.commit()
