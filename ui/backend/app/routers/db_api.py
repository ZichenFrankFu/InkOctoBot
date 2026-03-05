from __future__ import annotations
import sqlite3
from pathlib import Path
from fastapi import APIRouter, HTTPException, Query

from ..settings import settings
from ..utils import load_repo_config, get_db_path

router = APIRouter(prefix="/db", tags=["db"])


def _connect(db_path: str) -> sqlite3.Connection:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    return con


def _get_con():
    repo_cfg = load_repo_config(settings.repo_root)
    db_path = get_db_path(repo_cfg, settings.repo_root)
    return _connect(db_path)


# ─────────────────────────────────────────────
# Overview / Stats
# ─────────────────────────────────────────────

@router.get("/overview")
def overview(platform: str | None = None):
    """Aggregate stats for the dashboard."""
    with _get_con() as con:
        where = ""
        params: list = []
        if platform:
            where = " WHERE platform=?"
            params = [platform]

        novel_count = con.execute(
            f"SELECT COUNT(*) AS c FROM novels{where}", params
        ).fetchone()["c"]

        rank_list_count = con.execute(
            f"SELECT COUNT(*) AS c FROM rank_lists{' WHERE platform=?' if platform else ''}",
            [platform] if platform else [],
        ).fetchone()["c"]

        snapshot_count = con.execute(
            "SELECT COUNT(*) AS c FROM rank_snapshots"
            + (
                " WHERE rank_list_id IN (SELECT rank_list_id FROM rank_lists WHERE platform=?)"
                if platform
                else ""
            ),
            [platform] if platform else [],
        ).fetchone()["c"]

        chapter_count = con.execute(
            "SELECT COUNT(*) AS c FROM first_n_chapters"
            + (
                " WHERE novel_uid IN (SELECT novel_uid FROM novels WHERE platform=?)"
                if platform
                else ""
            ),
            [platform] if platform else [],
        ).fetchone()["c"]

        # Recent snapshots
        recent_sql = """
            SELECT s.snapshot_id, s.snapshot_date, s.item_count,
                   l.platform, l.rank_family, l.rank_sub_cat
            FROM rank_snapshots s
            JOIN rank_lists l ON l.rank_list_id = s.rank_list_id
        """
        if platform:
            recent_sql += " WHERE l.platform=?"
        recent_sql += " ORDER BY s.snapshot_date DESC, s.snapshot_id DESC LIMIT 20"

        recent = con.execute(recent_sql, [platform] if platform else []).fetchall()

        # Platform breakdown (always show)
        platform_breakdown = con.execute(
            "SELECT platform, COUNT(*) AS count FROM novels GROUP BY platform ORDER BY count DESC"
        ).fetchall()

        # Category breakdown
        cat_sql = """
            SELECT n.main_category, COUNT(*) AS count
            FROM novels n
        """
        if platform:
            cat_sql += " WHERE n.platform=?"
        cat_sql += " GROUP BY n.main_category ORDER BY count DESC LIMIT 15"
        categories = con.execute(cat_sql, [platform] if platform else []).fetchall()

        # Rank families
        fam_sql = """
            SELECT l.rank_family, l.platform, COUNT(DISTINCT s.snapshot_id) AS snapshot_count
            FROM rank_lists l
            LEFT JOIN rank_snapshots s ON s.rank_list_id = l.rank_list_id
        """
        if platform:
            fam_sql += " WHERE l.platform=?"
        fam_sql += " GROUP BY l.rank_family, l.platform ORDER BY snapshot_count DESC"
        rank_families = con.execute(fam_sql, [platform] if platform else []).fetchall()

    return {
        "novel_count": novel_count,
        "rank_list_count": rank_list_count,
        "snapshot_count": snapshot_count,
        "chapter_count": chapter_count,
        "recent_snapshots": [dict(r) for r in recent],
        "platform_breakdown": [dict(r) for r in platform_breakdown],
        "categories": [dict(r) for r in categories],
        "rank_families": [dict(r) for r in rank_families],
    }


# ─────────────────────────────────────────────
# Top novels (most frequently on rankings)
# ─────────────────────────────────────────────

@router.get("/top_novels")
def top_novels(
    platform: str | None = None,
    rank_family: str | None = None,
    limit: int = Query(default=30, ge=1, le=100),
):
    """Top novels by number of ranking appearances."""
    with _get_con() as con:
        conditions = []
        params: list = []

        if platform:
            conditions.append("l.platform=?")
            params.append(platform)
        if rank_family:
            conditions.append("l.rank_family=?")
            params.append(rank_family)

        where = (" WHERE " + " AND ".join(conditions)) if conditions else ""

        sql = f"""
            SELECT
                n.novel_uid,
                nt.title,
                n.author,
                n.platform,
                n.main_category,
                n.status,
                n.total_words,
                COUNT(DISTINCT e.snapshot_id) AS appearances,
                MIN(e.rank) AS best_rank,
                ROUND(AVG(e.rank), 1) AS avg_rank,
                MAX(s.snapshot_date) AS last_seen
            FROM rank_entries e
            JOIN rank_snapshots s ON s.snapshot_id = e.snapshot_id
            JOIN rank_lists l ON l.rank_list_id = s.rank_list_id
            JOIN novels n ON n.novel_uid = e.novel_uid
            LEFT JOIN novel_titles nt ON nt.novel_uid = n.novel_uid AND nt.is_primary = 1
            {where}
            GROUP BY n.novel_uid
            ORDER BY appearances DESC, best_rank ASC
            LIMIT ?
        """
        params.append(limit)
        rows = con.execute(sql, params).fetchall()

    return {"rows": [dict(r) for r in rows]}


# ─────────────────────────────────────────────
# Rank lists (with platform filter)
# ─────────────────────────────────────────────

@router.get("/rank_lists")
def rank_lists(platform: str | None = None):
    with _get_con() as con:
        q = "SELECT * FROM rank_lists"
        params: list = []
        if platform:
            q += " WHERE platform=?"
            params.append(platform)
        q += " ORDER BY platform, rank_family, rank_sub_cat"
        rows = con.execute(q, params).fetchall()
    return {"rows": [dict(r) for r in rows]}


# ─────────────────────────────────────────────
# Snapshots
# ─────────────────────────────────────────────

@router.get("/snapshots")
def snapshots(rank_list_id: int):
    with _get_con() as con:
        rows = con.execute(
            """SELECT s.*, l.platform, l.rank_family, l.rank_sub_cat
               FROM rank_snapshots s
               JOIN rank_lists l ON l.rank_list_id = s.rank_list_id
               WHERE s.rank_list_id=?
               ORDER BY s.snapshot_date DESC, s.snapshot_id DESC""",
            (rank_list_id,),
        ).fetchall()
    return {"rows": [dict(r) for r in rows]}


# ─────────────────────────────────────────────
# Entries (enriched with novel info)
# ─────────────────────────────────────────────

@router.get("/entries")
def entries_enriched(snapshot_id: int, limit: int = Query(default=200, ge=1, le=2000)):
    """Return rank entries with novel title, author, category joined in."""
    with _get_con() as con:
        rows = con.execute(
            """
            SELECT
                e.snapshot_id,
                e.novel_uid,
                e.rank,
                e.total_recommend,
                e.reading_count,
                e.extra_json,
                n.platform,
                n.author,
                n.main_category,
                n.status,
                n.total_words,
                n.url,
                nt.title
            FROM rank_entries e
            JOIN novels n ON n.novel_uid = e.novel_uid
            LEFT JOIN novel_titles nt ON nt.novel_uid = n.novel_uid AND nt.is_primary = 1
            WHERE e.snapshot_id = ?
            ORDER BY e.rank ASC
            LIMIT ?
            """,
            (snapshot_id, limit),
        ).fetchall()
    return {"rows": [dict(r) for r in rows]}


# ─────────────────────────────────────────────
# Novel detail
# ─────────────────────────────────────────────

@router.get("/novel/{novel_uid}")
def novel_detail(novel_uid: int):
    with _get_con() as con:
        n = con.execute("SELECT * FROM novels WHERE novel_uid=?", (novel_uid,)).fetchone()
        if not n:
            raise HTTPException(status_code=404, detail="novel not found")

        titles = con.execute(
            "SELECT * FROM novel_titles WHERE novel_uid=? ORDER BY last_seen_date DESC",
            (novel_uid,),
        ).fetchall()
        tags = con.execute(
            "SELECT t.* FROM tags t JOIN novel_tag_map m ON m.tag_id=t.tag_id WHERE m.novel_uid=? ORDER BY t.tag_name",
            (novel_uid,),
        ).fetchall()
        history = con.execute(
            """SELECT e.*, s.snapshot_date, l.rank_family, l.rank_sub_cat, l.platform
               FROM rank_entries e
               JOIN rank_snapshots s ON s.snapshot_id=e.snapshot_id
               JOIN rank_lists l ON l.rank_list_id=s.rank_list_id
               WHERE e.novel_uid=?
               ORDER BY s.snapshot_date DESC, e.rank ASC""",
            (novel_uid,),
        ).fetchall()
        chapters = con.execute(
            "SELECT chapter_id, novel_uid, chapter_num, chapter_title, word_count, publish_date FROM first_n_chapters WHERE novel_uid=? ORDER BY chapter_num ASC",
            (novel_uid,),
        ).fetchall()

    return {
        "novel": dict(n),
        "titles": [dict(r) for r in titles],
        "tags": [dict(r) for r in tags],
        "rank_history": [dict(r) for r in history],
        "chapters": [dict(r) for r in chapters],
    }


@router.get("/novel/{novel_uid}/chapter/{chapter_num}")
def novel_chapter_content(novel_uid: int, chapter_num: int):
    """Get full chapter content for reading."""
    with _get_con() as con:
        row = con.execute(
            "SELECT * FROM first_n_chapters WHERE novel_uid=? AND chapter_num=?",
            (novel_uid, chapter_num),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="chapter not found")
    return dict(row)


# ─────────────────────────────────────────────
# Tag analysis
# ─────────────────────────────────────────────

@router.get("/tag_stats")
def tag_stats(platform: str | None = None, limit: int = Query(default=30, ge=1, le=100)):
    """Tag frequency with optional platform filter."""
    with _get_con() as con:
        sql = """
            SELECT t.tag_name, COUNT(DISTINCT m.novel_uid) AS novel_count
            FROM tags t
            JOIN novel_tag_map m ON m.tag_id = t.tag_id
        """
        params: list = []
        if platform:
            sql += " JOIN novels n ON n.novel_uid = m.novel_uid WHERE n.platform=?"
            params.append(platform)
        sql += " GROUP BY t.tag_name ORDER BY novel_count DESC LIMIT ?"
        params.append(limit)
        rows = con.execute(sql, params).fetchall()
    return {"rows": [dict(r) for r in rows]}


# ─────────────────────────────────────────────
# DB info (kept for diagnostics)
# ─────────────────────────────────────────────

@router.get("/info")
def db_info():
    repo_cfg = load_repo_config(settings.repo_root)
    db_path = get_db_path(repo_cfg, settings.repo_root)
    return {"db_path": db_path}


@router.get("/tables")
def list_tables():
    with _get_con() as con:
        rows = con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
    return {"tables": [r["name"] for r in rows]}
