"""整平台多维度代表作选取 (Round-10 高级特征提取重构).

用户只选平台（不再单选榜单）→ select(platform, category="") 用不同维度的优秀
作品共同代表整个平台：总排行最高 / 上榜最多 / 热度最高 / 新书榜抽样，每部带
selection_reason；非新书榜优先（机制4）。
"""
from __future__ import annotations

import sqlite3

import pytest


@pytest.fixture
def dbs(tmp_path):
    from storage.market_schema import create_all
    from storage.project_schema import ensure_creation_tables
    from storage.market_extractor_schema import ensure_market_extractor_tables

    crawler = str(tmp_path / "c.db")
    proj = str(tmp_path / "p.db")
    con = sqlite3.connect(crawler)
    create_all(con)
    con.executescript(
        """
        INSERT INTO rank_lists(rank_list_id,platform,rank_family,rank_sub_cat)
        VALUES (1,'qidian','畅销榜','玄幻'),(2,'qidian','新书榜','新书');
        INSERT INTO rank_snapshots(snapshot_id,rank_list_id,snapshot_date)
        VALUES (1,1,date('now')),(2,2,date('now'));
        """
    )
    # novels 1-13 on the main board (rank 1..13, heat 高→低); novels 14-16
    # ONLY on the 新书榜（低热度，新生力量）。配额 3+7+3+3=16 故 13 主榜 + 3 新书。
    for uid in range(1, 17):
        con.execute(
            "INSERT INTO novels(novel_uid,platform,platform_novel_id,author,"
            "author_norm,intro,main_category,status,total_words,url,"
            "created_date,last_seen_date) VALUES "
            "(?,?,?,?,?,?,?,?,?,?,date('now'),date('now'))",
            (uid, "qidian", f"q{uid}", "作者", "作者", "简介", "玄幻",
             "ongoing", 300000, "http://x"),
        )
        if uid <= 13:
            con.execute(
                "INSERT INTO rank_entries(snapshot_id,novel_uid,rank,"
                "total_recommend,reading_count) VALUES (1,?,?,?,0)",
                (uid, uid, 1000 * (14 - uid)),
            )
        else:
            con.execute(
                "INSERT INTO rank_entries(snapshot_id,novel_uid,rank,"
                "total_recommend,reading_count) VALUES (2,?,?,?,0)",
                (uid, uid - 13, 40),   # novel_uid=14..16, rank=1..3, 低热度
            )
    con.commit()
    con.close()

    pc = sqlite3.connect(proj)
    ensure_creation_tables(pc)
    ensure_market_extractor_tables(pc)
    pc.commit()
    pc.close()
    return crawler, proj


def test_platform_selection_is_diverse_with_reasons(dbs):
    from ui.backend.app.services.market_extractor import representative_selector as rs
    crawler, proj = dbs
    res = rs.select(proj, "qidian", "", crawler_db=crawler, seed=7)
    assert res["selected"] >= 6
    rows = rs.list_selected(proj, "qidian", "")
    reasons = {r["selection_reason"] for r in rows}
    # 不同维度都覆盖到（总排行最高 来自主榜；新书榜抽样 来自仅在新书榜的作品）。
    assert "总排行最高" in reasons
    assert "新书榜抽样" in reasons
    # 非新书榜优先：rank-1 主榜作品（novel_uid 1）入选且标为总排行最高。
    top = next(r for r in rows if r["source_db_novel_id"] == "1")
    assert top["selection_reason"] == "总排行最高"


def test_migration_adds_selection_reason_column(tmp_path):
    """旧库 ALTER 进 selection_reason 列（幂等）。"""
    from storage.market_extractor_schema import ensure_market_extractor_tables
    p = str(tmp_path / "old.db")
    con = sqlite3.connect(p)
    # 迁移前的真实表结构（缺 selection_reason 列）。
    con.execute(
        "CREATE TABLE representative_works_pool ("
        " work_id TEXT PRIMARY KEY, platform TEXT NOT NULL, category TEXT NOT NULL,"
        " source_db_novel_id TEXT, rank_score REAL, collection_count INTEGER,"
        " comment_count INTEGER, monthly_ticket INTEGER, word_count INTEGER,"
        " last_updated_at TIMESTAMP, selected_at TIMESTAMP,"
        " selected_for_extraction INTEGER NOT NULL DEFAULT 1,"
        " selection_round INTEGER DEFAULT 1, is_holdout INTEGER NOT NULL DEFAULT 0)"
    )
    con.commit()
    ensure_market_extractor_tables(con)
    cols = {r[1] for r in con.execute(
        "PRAGMA table_info(representative_works_pool)").fetchall()}
    con.close()
    assert "selection_reason" in cols
