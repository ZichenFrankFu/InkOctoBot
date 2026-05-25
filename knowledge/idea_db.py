"""IdeaDB — the 灵感库 (inspiration library) data layer.

Free-text idea snippets that the 灵感搜索 page can store and
similarity-search. Lives in its own SQLite file (``data/idea.db``) so
the reference-works + inspirations surfaces stay decoupled.

Public methods mirror the old ReferenceDB.{create,get,list,update,delete}_inspiration
signatures so the inspirations router (``ui/backend/app/routers/reference/inspirations.py``)
keeps working without endpoint changes.
"""
from __future__ import annotations

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


class IdeaDB:
    """灵感库 data access layer."""

    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        from storage.idea_schema import ensure_idea_tables
        with _conn(self.db_path) as conn:
            ensure_idea_tables(conn)
        logger.info("idea_db opened path=%s", self.db_path)

    def create_inspiration(self, category: str, title: str, content: str) -> dict:
        iid = _gid("insp")
        with _conn(self.db_path) as c:
            c.execute(
                "INSERT INTO inspirations (id,category,title,content) "
                "VALUES (?,?,?,?)",
                (iid, category or "other", title or "", content or ""),
            )
            c.commit()
        logger.info("idea_db create id=%s category=%s", iid, category or "other")
        return self.get_inspiration(iid)  # type: ignore[return-value]

    def get_inspiration(self, insp_id: str) -> dict | None:
        with _conn(self.db_path) as c:
            r = c.execute(
                "SELECT * FROM inspirations WHERE id=?", (insp_id,),
            ).fetchone()
        return dict(r) if r else None

    def list_inspirations(self) -> list[dict]:
        with _conn(self.db_path) as c:
            rows = c.execute(
                "SELECT * FROM inspirations ORDER BY updated_at DESC",
            ).fetchall()
        return [dict(r) for r in rows]

    def update_inspiration(self, insp_id: str, **fields: Any) -> dict | None:
        allowed = {"category", "title", "content"}
        sets = ["updated_at=CURRENT_TIMESTAMP"]
        params: list[Any] = []
        for k, v in fields.items():
            if k in allowed and v is not None:
                sets.append(f"{k}=?")
                params.append(v)
        if len(sets) == 1:
            return self.get_inspiration(insp_id)
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
