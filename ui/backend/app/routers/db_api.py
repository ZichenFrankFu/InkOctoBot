from __future__ import annotations
import logging
import sqlite3
from pathlib import Path
from fastapi import APIRouter, HTTPException, Query
from ..settings import settings
from ..utils import load_repo_config, get_crawler_db_path

router = APIRouter(prefix="/db", tags=["db"])
logger = logging.getLogger("inkoctobot.ui.backend.db_api")

def _get_con() -> sqlite3.Connection | None:
    """Return a DB connection, or *None* when the crawler DB is unavailable."""
    # Test mode: use crawler DB from data_dir
    if settings.test_mode and settings.data_dir:
        test_db = settings.data_dir / "InkOctoBot_Crawler.db"
        if test_db.exists():
            con = sqlite3.connect(str(test_db)); con.row_factory = sqlite3.Row; return con
    # Check if user has set a custom crawler DB path in settings
    try:
        import json
        settings_file = settings.get_data_path("settings.json")
        if settings_file.exists():
            user_settings = json.loads(settings_file.read_text("utf-8"))
            custom_path = user_settings.get("crawler_db_path", "")
            if custom_path:
                p = Path(custom_path)
                if p.exists():
                    con = sqlite3.connect(str(p)); con.row_factory = sqlite3.Row; return con
    except Exception:
        pass
    try:
        repo_cfg = load_repo_config(settings.repo_root)
        db_path = get_crawler_db_path(repo_cfg, settings.repo_root)
    except Exception:
        logger.warning("Could not resolve crawler DB path — returning empty data")
        return None
    if not Path(db_path).exists():
        logger.info("Crawler DB file not found at %s — returning empty data", db_path)
        return None
    con = sqlite3.connect(db_path); con.row_factory = sqlite3.Row; return con

def _table_exists(con: sqlite3.Connection, name: str) -> bool:
    return con.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone()[0] > 0

_OVERVIEW_EMPTY = {"novel_count": 0, "rank_list_count": 0, "snapshot_count": 0, "chapter_count": 0, "recent_snapshots": [], "platform_breakdown": [], "categories": [], "rank_families": []}

@router.get("/overview")
def overview(platform: str | None = None):
    con = _get_con()
    if con is None:
        return _OVERVIEW_EMPTY
    with con:
        if not _table_exists(con, "novels"):
            return _OVERVIEW_EMPTY
        pw = " WHERE platform=?" if platform else ""
        pp = [platform] if platform else []
        novel_count = con.execute(f"SELECT COUNT(*) AS c FROM novels{pw}", pp).fetchone()["c"]
        rank_list_count = con.execute(f"SELECT COUNT(*) AS c FROM rank_lists{' WHERE platform=?' if platform else ''}", pp).fetchone()["c"]
        snapshot_count = con.execute("SELECT COUNT(*) AS c FROM rank_snapshots" + (" WHERE rank_list_id IN (SELECT rank_list_id FROM rank_lists WHERE platform=?)" if platform else ""), pp).fetchone()["c"]
        chapter_count = con.execute("SELECT COUNT(*) AS c FROM first_n_chapters" + (" WHERE novel_uid IN (SELECT novel_uid FROM novels WHERE platform=?)" if platform else ""), pp).fetchone()["c"]
        recent_sql = "SELECT s.snapshot_id,s.snapshot_date,s.item_count,l.platform,l.rank_family,l.rank_sub_cat FROM rank_snapshots s JOIN rank_lists l ON l.rank_list_id=s.rank_list_id" + (" WHERE l.platform=?" if platform else "") + " ORDER BY s.snapshot_date DESC LIMIT 20"
        recent = con.execute(recent_sql, pp).fetchall()
        pb = con.execute("SELECT platform, COUNT(*) AS count FROM novels GROUP BY platform").fetchall()
        cat_sql = "SELECT n.main_category, COUNT(*) AS count FROM novels n" + (" WHERE n.platform=?" if platform else "") + " GROUP BY n.main_category ORDER BY count DESC LIMIT 15"
        cats = con.execute(cat_sql, pp).fetchall()
        fam_sql = "SELECT l.rank_family,l.platform,COUNT(DISTINCT s.snapshot_id) AS snapshot_count FROM rank_lists l LEFT JOIN rank_snapshots s ON s.rank_list_id=l.rank_list_id" + (" WHERE l.platform=?" if platform else "") + " GROUP BY l.rank_family,l.platform ORDER BY snapshot_count DESC"
        fams = con.execute(fam_sql, pp).fetchall()
    return {"novel_count": novel_count, "rank_list_count": rank_list_count, "snapshot_count": snapshot_count, "chapter_count": chapter_count, "recent_snapshots": [dict(r) for r in recent], "platform_breakdown": [dict(r) for r in pb], "categories": [dict(r) for r in cats], "rank_families": [dict(r) for r in fams]}

@router.get("/top_novels")
def top_novels(platform: str | None = None, rank_family: str | None = None, limit: int = Query(default=30, ge=1, le=100)):
    con = _get_con()
    if con is None:
        return {"rows": []}
    with con:
        if not _table_exists(con, "rank_entries"):
            return {"rows": []}
        conds, params = [], []
        if platform: conds.append("l.platform=?"); params.append(platform)
        if rank_family: conds.append("l.rank_family=?"); params.append(rank_family)
        where = (" WHERE " + " AND ".join(conds)) if conds else ""
        params.append(limit)
        rows = con.execute(f"SELECT n.novel_uid,nt.title,n.author,n.platform,n.main_category,n.status,n.total_words,COUNT(DISTINCT e.snapshot_id) AS appearances,MIN(e.rank) AS best_rank,ROUND(AVG(e.rank),1) AS avg_rank,MAX(s.snapshot_date) AS last_seen FROM rank_entries e JOIN rank_snapshots s ON s.snapshot_id=e.snapshot_id JOIN rank_lists l ON l.rank_list_id=s.rank_list_id JOIN novels n ON n.novel_uid=e.novel_uid LEFT JOIN novel_titles nt ON nt.novel_uid=n.novel_uid AND nt.is_primary=1{where} GROUP BY n.novel_uid ORDER BY appearances DESC,best_rank ASC LIMIT ?", params).fetchall()
    return {"rows": [dict(r) for r in rows]}

@router.get("/rank_lists")
def rank_lists(platform: str | None = None):
    con = _get_con()
    if con is None:
        return {"rows": []}
    with con:
        if not _table_exists(con, "rank_lists"):
            return {"rows": []}
        q = "SELECT * FROM rank_lists"; p = []
        if platform: q += " WHERE platform=?"; p.append(platform)
        q += " ORDER BY platform,rank_family,rank_sub_cat"
        return {"rows": [dict(r) for r in con.execute(q, p).fetchall()]}

@router.get("/snapshots")
def snapshots(rank_list_id: int):
    con = _get_con()
    if con is None:
        return {"rows": []}
    with con:
        if not _table_exists(con, "rank_snapshots"):
            return {"rows": []}
        rows = con.execute("SELECT s.*,l.platform,l.rank_family,l.rank_sub_cat FROM rank_snapshots s JOIN rank_lists l ON l.rank_list_id=s.rank_list_id WHERE s.rank_list_id=? ORDER BY s.snapshot_date DESC", (rank_list_id,)).fetchall()
    return {"rows": [dict(r) for r in rows]}

@router.get("/entries")
def entries_enriched(snapshot_id: int, limit: int = Query(default=200, ge=1, le=2000)):
    con = _get_con()
    if con is None:
        return {"rows": []}
    with con:
        if not _table_exists(con, "rank_entries"):
            return {"rows": []}
        rows = con.execute("SELECT e.snapshot_id,e.novel_uid,e.rank,e.total_recommend,e.reading_count,e.extra_json,n.platform,n.author,n.main_category,n.status,n.total_words,n.url,nt.title FROM rank_entries e JOIN novels n ON n.novel_uid=e.novel_uid LEFT JOIN novel_titles nt ON nt.novel_uid=n.novel_uid AND nt.is_primary=1 WHERE e.snapshot_id=? ORDER BY e.rank ASC LIMIT ?", (snapshot_id, limit)).fetchall()
    return {"rows": [dict(r) for r in rows]}

@router.get("/novel/{novel_uid}")
def novel_detail(novel_uid: int):
    """Returns novel info. Chapters: only metadata (title, word_count), NO content."""
    con = _get_con()
    if con is None:
        raise HTTPException(404, "Crawler DB not available")
    with con:
        if not _table_exists(con, "novels"):
            raise HTTPException(404, "novel not found")
        n = con.execute("SELECT * FROM novels WHERE novel_uid=?", (novel_uid,)).fetchone()
        if not n: raise HTTPException(404, "novel not found")
        titles = con.execute("SELECT * FROM novel_titles WHERE novel_uid=? ORDER BY last_seen_date DESC", (novel_uid,)).fetchall()
        tags = con.execute("SELECT t.* FROM tags t JOIN novel_tag_map m ON m.tag_id=t.tag_id WHERE m.novel_uid=? ORDER BY t.tag_name", (novel_uid,)).fetchall()
        history = con.execute("SELECT e.*,s.snapshot_date,l.rank_family,l.rank_sub_cat,l.platform FROM rank_entries e JOIN rank_snapshots s ON s.snapshot_id=e.snapshot_id JOIN rank_lists l ON l.rank_list_id=s.rank_list_id WHERE e.novel_uid=? ORDER BY s.snapshot_date DESC,e.rank ASC", (novel_uid,)).fetchall()
        # Only return chapter metadata — NO chapter_content
        chapters = con.execute("SELECT chapter_id,novel_uid,chapter_num,chapter_title,word_count,publish_date FROM first_n_chapters WHERE novel_uid=? ORDER BY chapter_num ASC", (novel_uid,)).fetchall()
    return {"novel": dict(n), "titles": [dict(r) for r in titles], "tags": [dict(r) for r in tags], "rank_history": [dict(r) for r in history], "chapters": [dict(r) for r in chapters]}

@router.get("/tag_stats")
def tag_stats(platform: str | None = None, limit: int = Query(default=30, ge=1, le=100)):
    con = _get_con()
    if con is None:
        return {"rows": []}
    with con:
        if not _table_exists(con, "tags"):
            return {"rows": []}
        sql = "SELECT t.tag_name, COUNT(DISTINCT m.novel_uid) AS novel_count FROM tags t JOIN novel_tag_map m ON m.tag_id=t.tag_id"
        p = []
        if platform: sql += " JOIN novels n ON n.novel_uid=m.novel_uid WHERE n.platform=?"; p.append(platform)
        sql += " GROUP BY t.tag_name ORDER BY novel_count DESC LIMIT ?"; p.append(limit)
        return {"rows": [dict(r) for r in con.execute(sql, p).fetchall()]}

@router.get("/info")
def db_info():
    if settings.test_mode and settings.data_dir:
        test_db = settings.data_dir / "InkOctoBot_Crawler.db"
        return {"db_path": str(test_db), "available": test_db.exists(), "test_mode": True}
    try:
        repo_cfg = load_repo_config(settings.repo_root)
        db_path = get_crawler_db_path(repo_cfg, settings.repo_root)
    except Exception:
        db_path = None
    return {"db_path": db_path, "available": db_path is not None and Path(db_path).exists()}

@router.get("/tables")
def list_tables():
    con = _get_con()
    if con is None:
        return {"tables": []}
    with con:
        return {"tables": [r["name"] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()]}
