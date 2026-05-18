"""
Reference database manager.

Manages reference_works + reference_entries tables (SQLite).
Schema defined in database/reference_schema.py (README §5.2.2).
"""
from __future__ import annotations
import json, logging, sqlite3, uuid
from pathlib import Path
from typing import Any

logger = logging.getLogger("inkoctobot.rag.reference_db")

def _gid(prefix: str = "ref") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"

def _conn(db_path: str | Path) -> sqlite3.Connection:
    c = sqlite3.connect(str(db_path))
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA foreign_keys=ON")
    c.row_factory = sqlite3.Row
    return c


class ReferenceDB:
    """CRUD for reference_works, reference_entries, project_reference_links."""

    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        from database.reference_schema import ensure_reference_tables
        with _conn(self.db_path) as conn:
            ensure_reference_tables(conn)

    # ── reference_works ──────────────────────────────────

    def create_work(self, title: str, media_type: str = "web_novel",
                    source: str = "manual", **kw: Any) -> dict:
        rid = _gid("ref")
        pre = ("pending" if (kw.get("has_full_text") and source == "file_upload")
               else "not_applicable")
        with _conn(self.db_path) as c:
            c.execute(
                """INSERT INTO reference_works
                   (ref_id,title,creator,media_type,genre,tags_json,source,
                    platform,novel_uid,file_path,user_rating,user_summary,
                    user_why_i_like,learning_dimensions_json,has_full_text,
                    preprocessing_status,serial_status)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (rid, title, kw.get("creator", ""), media_type,
                 kw.get("genre", ""),
                 json.dumps(kw.get("tags", []), ensure_ascii=False),
                 source, kw.get("platform"), kw.get("novel_uid"),
                 kw.get("file_path"), kw.get("user_rating"),
                 kw.get("user_summary"), kw.get("user_why_i_like"),
                 json.dumps(kw.get("learning_dimensions", []), ensure_ascii=False),
                 int(kw.get("has_full_text", False)), pre,
                 kw.get("serial_status")))
            c.commit()
        return self.get_work(rid)  # type: ignore

    def get_work(self, ref_id: str) -> dict | None:
        with _conn(self.db_path) as c:
            r = c.execute("SELECT * FROM reference_works WHERE ref_id=?",
                          (ref_id,)).fetchone()
        return dict(r) if r else None

    def list_works(self, *, media_type: str | None = None,
                   source: str | None = None, genre: str | None = None,
                   search: str | None = None,
                   preprocessing_status: str | None = None,
                   limit: int = 100, offset: int = 0) -> list[dict]:
        conds: list[str] = []
        params: list[Any] = []
        if media_type:
            conds.append("media_type=?"); params.append(media_type)
        if source:
            conds.append("source=?"); params.append(source)
        if genre:
            conds.append("genre LIKE ?"); params.append(f"%{genre}%")
        if preprocessing_status:
            conds.append("preprocessing_status=?"); params.append(preprocessing_status)
        if search:
            conds.append("(title LIKE ? OR creator LIKE ?)")
            params += [f"%{search}%", f"%{search}%"]
        where = ("WHERE " + " AND ".join(conds)) if conds else ""
        with _conn(self.db_path) as c:
            rows = c.execute(
                f"SELECT * FROM reference_works {where} "
                f"ORDER BY updated_at DESC LIMIT ? OFFSET ?",
                params + [limit, offset],
            ).fetchall()
        return [dict(r) for r in rows]

    def count_works(self, **filters: Any) -> int:
        conds: list[str] = []
        params: list[Any] = []
        if filters.get("media_type"):
            conds.append("media_type=?"); params.append(filters["media_type"])
        if filters.get("preprocessing_status"):
            conds.append("preprocessing_status=?")
            params.append(filters["preprocessing_status"])
        where = ("WHERE " + " AND ".join(conds)) if conds else ""
        with _conn(self.db_path) as c:
            return c.execute(
                f"SELECT COUNT(*) FROM reference_works {where}", params
            ).fetchone()[0]

    def update_work(self, ref_id: str, **fields: Any) -> dict | None:
        allowed = {
            "title", "creator", "media_type", "genre", "tags_json",
            "user_rating", "user_summary", "user_why_i_like",
            "learning_dimensions_json", "has_full_text",
            "preprocessing_status", "style_fingerprint_json",
            "narrative_structure_json", "extracted_characters_json",
            "rhythm_template_json", "plot_outline_json", "segments_json",
            "settings_json", "serial_status", "rhythm_json", "file_path",
        }
        sets = ["updated_at=CURRENT_TIMESTAMP"]
        params: list[Any] = []
        for k, v in fields.items():
            if k in allowed:
                sets.append(f"{k}=?")
                params.append(v)
        if len(sets) == 1:
            return self.get_work(ref_id)
        params.append(ref_id)
        with _conn(self.db_path) as c:
            c.execute(
                f"UPDATE reference_works SET {', '.join(sets)} WHERE ref_id=?",
                params,
            )
            c.commit()
        return self.get_work(ref_id)

    def delete_work(self, ref_id: str) -> bool:
        with _conn(self.db_path) as c:
            c.execute("DELETE FROM reference_entries WHERE ref_id=?", (ref_id,))
            c.execute("DELETE FROM project_reference_links WHERE ref_id=?", (ref_id,))
            cur = c.execute("DELETE FROM reference_works WHERE ref_id=?", (ref_id,))
            c.commit()
        return cur.rowcount > 0

    def batch_import_from_crawl(self, *, min_rank: int = 50,
                                platforms: list[str] | None = None) -> list[dict]:
        pf = platforms or ["qidian", "fanqie"]
        ph = ",".join("?" * len(pf))
        with _conn(self.db_path) as c:
            rows = c.execute(f"""
                SELECT DISTINCT n.novel_uid, n.platform, nt.title, n.author,
                       n.main_category AS genre, n.total_words, n.status
                FROM novels n
                JOIN novel_titles nt ON n.novel_uid=nt.novel_uid AND nt.is_primary=1
                JOIN rank_entries re ON n.novel_uid=re.novel_uid
                WHERE n.platform IN ({ph}) AND re.rank<=?
                  AND EXISTS (SELECT 1 FROM first_n_chapters fc
                              WHERE fc.novel_uid=n.novel_uid)
            """, [*pf, min_rank]).fetchall()
            existing = {
                (r["platform"], r["novel_uid"])
                for r in c.execute(
                    "SELECT platform, novel_uid FROM reference_works "
                    "WHERE source='platform_crawl' AND novel_uid IS NOT NULL"
                ).fetchall()
            }
        imported: list[dict] = []
        for r in rows:
            if (r["platform"], r["novel_uid"]) in existing:
                continue
            w = self.create_work(
                title=r["title"], media_type="web_novel",
                source="platform_crawl",
                creator=r["author"] or "", genre=r["genre"] or "",
                platform=r["platform"], novel_uid=r["novel_uid"],
                has_full_text=True,
            )
            imported.append(w)
        return imported

    # ── reference_entries ─────────────────────────────────

    def add_entry(self, ref_id: str, entry_type: str, content: str,
                  **kw: Any) -> dict:
        eid = _gid("ent")
        with _conn(self.db_path) as c:
            c.execute(
                """INSERT INTO reference_entries
                   (entry_id,ref_id,entry_type,title,content,content_source,
                    position_label,user_notes,learning_dimensions_json,
                    user_rating,tags_json)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (eid, ref_id, entry_type, kw.get("title", ""), content,
                 kw.get("content_source", "user_written"),
                 kw.get("position_label", ""), kw.get("user_notes", ""),
                 json.dumps(kw.get("learning_dimensions", []), ensure_ascii=False),
                 kw.get("user_rating"),
                 json.dumps(kw.get("tags", []), ensure_ascii=False)))
            c.commit()
        return self.get_entry(eid)  # type: ignore

    def get_entry(self, entry_id: str) -> dict | None:
        with _conn(self.db_path) as c:
            r = c.execute("SELECT * FROM reference_entries WHERE entry_id=?",
                          (entry_id,)).fetchone()
        return dict(r) if r else None

    def list_entries(self, ref_id: str,
                     entry_type: str | None = None) -> list[dict]:
        conds = ["ref_id=?"]
        params: list[Any] = [ref_id]
        if entry_type:
            conds.append("entry_type=?"); params.append(entry_type)
        with _conn(self.db_path) as c:
            rows = c.execute(
                f"SELECT * FROM reference_entries "
                f"WHERE {' AND '.join(conds)} ORDER BY created_at",
                params,
            ).fetchall()
        return [dict(r) for r in rows]

    def delete_entry(self, entry_id: str) -> bool:
        with _conn(self.db_path) as c:
            cur = c.execute("DELETE FROM reference_entries WHERE entry_id=?",
                            (entry_id,))
            c.commit()
        return cur.rowcount > 0

    # ── project_reference_links ───────────────────────────

    def link_to_project(self, project_id: str, ref_id: str,
                        dimension: str, **kw: Any) -> dict:
        lid = _gid("lnk")
        with _conn(self.db_path) as c:
            c.execute(
                """INSERT INTO project_reference_links
                   (link_id,project_id,ref_id,dimension,entry_ids_json,
                    reference_character_name,notes)
                   VALUES (?,?,?,?,?,?,?)""",
                (lid, project_id, ref_id, dimension,
                 json.dumps(kw.get("entry_ids", []), ensure_ascii=False),
                 kw.get("reference_character_name"), kw.get("notes")))
            c.commit()
        return {"link_id": lid}

    def get_project_links(self, project_id: str) -> list[dict]:
        with _conn(self.db_path) as c:
            rows = c.execute(
                """SELECT l.*, rw.title, rw.media_type, rw.creator
                   FROM project_reference_links l
                   JOIN reference_works rw ON l.ref_id=rw.ref_id
                   WHERE l.project_id=?""",
                (project_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    # ── aggregation ───────────────────────────────────────

    def genre_distribution(self) -> list[dict]:
        with _conn(self.db_path) as c:
            rows = c.execute(
                """SELECT genre, media_type, COUNT(*) as cnt,
                          AVG(user_rating) as avg_rating
                   FROM reference_works
                   WHERE genre IS NOT NULL AND genre != ''
                   GROUP BY genre, media_type ORDER BY cnt DESC"""
            ).fetchall()
        return [dict(r) for r in rows]

    def get_pending(self, limit: int = 50) -> list[dict]:
        return self.list_works(preprocessing_status="pending", limit=limit)