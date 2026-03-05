from __future__ import annotations

import sqlite3
from fastapi import APIRouter

from ..settings import settings
from ..utils import get_db_path, load_repo_config

router = APIRouter(prefix="/analysis", tags=["analysis"])


def _connect(db_path: str) -> sqlite3.Connection:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    return con


@router.get("/dashboard")
def analysis_dashboard():
    repo_cfg = load_repo_config(settings.repo_root)
    db_path = get_db_path(repo_cfg, settings.repo_root)

    with _connect(db_path) as con:
        overview = con.execute(
            """
            SELECT
              (SELECT COUNT(*) FROM novels) AS novel_count,
              (SELECT COUNT(*) FROM rank_entries) AS rank_entry_count,
              (SELECT COUNT(*) FROM rank_snapshots) AS snapshot_count,
              (SELECT COUNT(*) FROM tags) AS tag_count,
              (SELECT MIN(snapshot_date) FROM rank_snapshots) AS earliest,
              (SELECT MAX(snapshot_date) FROM rank_snapshots) AS latest
            """
        ).fetchone()

        platform_rows = con.execute(
            "SELECT platform, COUNT(*) AS c FROM novels GROUP BY platform ORDER BY c DESC"
        ).fetchall()

        trend_rows = con.execute(
            """
            SELECT
              s.snapshot_date,
              COUNT(*) AS entry_count,
              COUNT(DISTINCT e.novel_uid) AS novel_count
            FROM rank_entries e
            JOIN rank_snapshots s ON s.snapshot_id=e.snapshot_id
            GROUP BY s.snapshot_date
            ORDER BY s.snapshot_date DESC
            LIMIT 30
            """
        ).fetchall()

        tag_rows = con.execute(
            """
            SELECT t.tag_name, COUNT(*) AS c
            FROM novel_tag_map m
            JOIN tags t ON t.tag_id=m.tag_id
            GROUP BY t.tag_id, t.tag_name
            ORDER BY c DESC
            LIMIT 12
            """
        ).fetchall()

        top_novels = con.execute(
            """
            SELECT
              n.novel_uid,
              COALESCE(n.novel_name, (SELECT t2.title FROM novel_titles t2 WHERE t2.novel_uid=n.novel_uid ORDER BY t2.is_primary DESC, t2.last_seen_date DESC LIMIT 1), '') AS novel_name,
              n.author,
              AVG(e.rank) AS avg_rank,
              COUNT(*) AS appearances
            FROM rank_entries e
            JOIN novels n ON n.novel_uid=e.novel_uid
            GROUP BY n.novel_uid
            HAVING appearances >= 2
            ORDER BY appearances DESC, avg_rank ASC
            LIMIT 10
            """
        ).fetchall()

    return {
        "summary": dict(overview) if overview else {},
        "platform_distribution": [dict(r) for r in platform_rows],
        "trend": [dict(r) for r in reversed(trend_rows)],
        "top_tags": [dict(r) for r in tag_rows],
        "top_novels": [dict(r) for r in top_novels],
    }
