"""
Layer 4: Episodic Timeline (SQLite).

Structured storage for key events, causal chains, foreshadowing status,
and timeline queries.  Supports queries like:
  - "第3章埋的伏笔到现在回收了吗？"
  - "角色A和B上次见面是哪章？"
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
import uuid
from typing import Any

logger = logging.getLogger("inkoctobot.rag.memory.episodic_timeline")


class EpisodicTimeline:
    """Layer 4 — structured event timeline with causal tracking."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._local = threading.local()

    def _conn(self) -> sqlite3.Connection:
        c = getattr(self._local, "conn", None)
        if c is None:
            c = sqlite3.connect(self.db_path, check_same_thread=False)
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA foreign_keys=ON")
            self._local.conn = c
        return c

    def add_event(
        self,
        project_id: str,
        chapter_num: int,
        event_type: str,
        description: str,
        *,
        scene_index: int = 0,
        characters: list[str] | None = None,
        causality: dict[str, Any] | None = None,
        foreshadow_status: str | None = None,
        foreshadow_target: int | None = None,
        importance: int = 3,
    ) -> str:
        eid = f"evt_{uuid.uuid4().hex[:12]}"
        with self._conn() as c:
            c.execute(
                """INSERT INTO episodic_events
                   (event_id, project_id, chapter_num, scene_index, event_type,
                    description, characters_json, causality_json,
                    foreshadow_status, foreshadow_target_chapter, importance)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (eid, project_id, chapter_num, scene_index, event_type,
                 description,
                 json.dumps(characters or [], ensure_ascii=False),
                 json.dumps(causality or {}, ensure_ascii=False),
                 foreshadow_status, foreshadow_target, importance),
            )
            c.commit()
        return eid

    def get_chapter_events(self, project_id: str, chapter_num: int) -> list[dict]:
        with self._conn() as c:
            rows = c.execute(
                """SELECT * FROM episodic_events
                   WHERE project_id=? AND chapter_num=?
                   ORDER BY scene_index""",
                (project_id, chapter_num),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_timeline(self, project_id: str, *, from_chapter: int = 0,
                     to_chapter: int = 9999) -> list[dict]:
        with self._conn() as c:
            rows = c.execute(
                """SELECT * FROM episodic_events
                   WHERE project_id=? AND chapter_num BETWEEN ? AND ?
                   ORDER BY chapter_num, scene_index""",
                (project_id, from_chapter, to_chapter),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_unresolved_foreshadowing(self, project_id: str) -> list[dict]:
        with self._conn() as c:
            rows = c.execute(
                """SELECT * FROM episodic_events
                   WHERE project_id=? AND foreshadow_status='planted'
                   ORDER BY chapter_num""",
                (project_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def resolve_foreshadowing(self, event_id: str, resolution_chapter: int) -> None:
        with self._conn() as c:
            c.execute(
                """UPDATE episodic_events
                   SET foreshadow_status='resolved', foreshadow_target_chapter=?
                   WHERE event_id=?""",
                (resolution_chapter, event_id),
            )
            c.commit()

    def get_character_events(self, project_id: str, character_name: str) -> list[dict]:
        with self._conn() as c:
            rows = c.execute(
                """SELECT * FROM episodic_events
                   WHERE project_id=? AND characters_json LIKE ?
                   ORDER BY chapter_num, scene_index""",
                (project_id, f'%"{character_name}"%'),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_character_last_meeting(
        self, project_id: str, char_a: str, char_b: str,
    ) -> dict | None:
        with self._conn() as c:
            rows = c.execute(
                """SELECT * FROM episodic_events
                   WHERE project_id=?
                     AND characters_json LIKE ?
                     AND characters_json LIKE ?
                   ORDER BY chapter_num DESC, scene_index DESC LIMIT 1""",
                (project_id, f'%"{char_a}"%', f'%"{char_b}"%'),
            ).fetchall()
        return dict(rows[0]) if rows else None

    def get_event_stats(self, project_id: str) -> dict[str, Any]:
        with self._conn() as c:
            total = c.execute(
                "SELECT COUNT(*) FROM episodic_events WHERE project_id=?",
                (project_id,),
            ).fetchone()[0]
            by_type = c.execute(
                """SELECT event_type, COUNT(*) as cnt FROM episodic_events
                   WHERE project_id=? GROUP BY event_type""",
                (project_id,),
            ).fetchall()
            unresolved = c.execute(
                """SELECT COUNT(*) FROM episodic_events
                   WHERE project_id=? AND foreshadow_status='planted'""",
                (project_id,),
            ).fetchone()[0]
        return {
            "total_events": total,
            "by_type": {r["event_type"]: r["cnt"] for r in by_type},
            "unresolved_foreshadowing": unresolved,
        }
