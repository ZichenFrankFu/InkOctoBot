"""
/api/analysis — Wraps TrendAnalyzer for interactive frontend.
Handles import errors gracefully (analysis/ may not be installed).

``/run`` is served through the persistent compute cache: the pandas
pipeline takes seconds-to-minutes over a real crawler DB, and running
it inside the request thread used to drain the anyio worker pool (the
全站无限加载 bug). Responses are now instant — last finished result
(with a ``stale`` flag when the DB changed) or ``{state:'computing'}``
while a single background thread does the work.
"""
from __future__ import annotations
import sqlite3, sys, traceback, math
from datetime import date, timedelta
from pathlib import Path
from typing import Any
import numpy as np, pandas as pd
from fastapi import APIRouter, HTTPException, Query
from ..settings import settings
from ..utils import resolve_crawler_db_path, crawler_db_version
from ..services import compute_cache
from ..services.project_paths import get_db_path as _project_db_path

router = APIRouter(prefix="/analysis", tags=["analysis"])

def _safe_records(df, max_rows=200):
    if df is None or (hasattr(df, 'empty') and df.empty): return []
    d = df.head(max_rows).copy()
    d = d.replace([np.inf, -np.inf], np.nan)
    for c in d.columns:
        if pd.api.types.is_datetime64_any_dtype(d[c]): d[c] = d[c].astype(str)
    records = d.to_dict(orient="records")
    # Sanitize at Python level — pandas .where(None) doesn't reliably kill NaN
    def _clean(v):
        if isinstance(v, float) and (math.isnan(v) or math.isinf(v)): return None
        return v
    return [{k: _clean(v) for k, v in row.items()} for row in records]

def _db_date_range(db_path):
    if not Path(db_path).exists():
        return ("", "")
    con = sqlite3.connect(db_path)
    try:
        r = pd.read_sql_query("SELECT MIN(snapshot_date) AS mn, MAX(snapshot_date) AS mx FROM rank_snapshots", con).iloc[0]
    except Exception:
        con.close()
        return ("", "")
    con.close()
    return (str(r["mn"]) if pd.notna(r["mn"]) else "", str(r["mx"]) if pd.notna(r["mx"]) else "")

def _compute_window(db_path, lookback):
    mn, _ = _db_date_range(db_path)
    if not mn: return "", ""
    end_d = date.today()
    if lookback == "all": start_d = pd.to_datetime(mn).date()
    else:
        days = {"week": 7, "month": 30, "quarter": 90, "year": 365}.get(lookback, 9999)
        start_d = max(end_d - timedelta(days=days), pd.to_datetime(mn).date())
    return start_d.isoformat(), end_d.isoformat()

@router.get("/date_range")
def analysis_date_range():
    db_path = resolve_crawler_db_path()
    mn, mx = _db_date_range(db_path)
    return {"min_date": mn, "max_date": mx}

def _db_category_tag_counts(db_path: str, platform: str,
                            start_date: str, end_date: str) -> dict:
    """Per-category / per-tag NOVEL counts straight from the novels table —
    this is the real「数量」(how many novels in the DB belong to a
    category), distinct from the rank-rollup appearance counts. Also
    returns NEW-book counts (created within the window) so the UI can show
    a cross-category new-book share that sums to 100%."""
    out = {"cat_total": {}, "cat_new": {}, "tag_total": {}, "tag_new": {}}
    if not Path(db_path).exists():
        return out
    plat_ok = platform in ("qidian", "fanqie")
    pw = " WHERE platform = ?" if plat_ok else ""
    pp: list = [platform] if plat_ok else []
    try:
        con = sqlite3.connect(db_path)
        for cat, c in con.execute(
                f"SELECT main_category, COUNT(*) FROM novels{pw} "
                f"GROUP BY main_category", pp):
            if cat:
                out["cat_total"][cat] = int(c)
        nw = (pw + " AND" if pw else " WHERE") + " created_date >= ? AND created_date <= ?"
        for cat, c in con.execute(
                f"SELECT main_category, COUNT(*) FROM novels{nw} "
                f"GROUP BY main_category", pp + [start_date, end_date]):
            if cat:
                out["cat_new"][cat] = int(c)
        tw = " WHERE n.platform = ?" if plat_ok else ""
        for tag, c in con.execute(
                "SELECT t.tag_name, COUNT(DISTINCT m.novel_uid) "
                "FROM novel_tag_map m JOIN tags t ON t.tag_id = m.tag_id "
                f"JOIN novels n ON n.novel_uid = m.novel_uid{tw} "
                "GROUP BY t.tag_name", pp):
            if tag:
                out["tag_total"][tag] = int(c)
        tnw = (tw + " AND" if tw else " WHERE") + " n.created_date >= ? AND n.created_date <= ?"
        for tag, c in con.execute(
                "SELECT t.tag_name, COUNT(DISTINCT m.novel_uid) "
                "FROM novel_tag_map m JOIN tags t ON t.tag_id = m.tag_id "
                f"JOIN novels n ON n.novel_uid = m.novel_uid{tnw} "
                "GROUP BY t.tag_name", pp + [start_date, end_date]):
            if tag:
                out["tag_new"][tag] = int(c)
        con.close()
    except sqlite3.OperationalError:
        pass
    return out


def _compute_analysis(db_path: str, platform: str, lookback: str, top_k: int) -> dict:
    """Heavy pandas pipeline — only ever runs on a compute_cache thread."""
    # Ensure repo root is importable
    root = str(settings.repo_root)
    if root not in sys.path: sys.path.insert(0, root)

    start_date, end_date = _compute_window(db_path, lookback)
    if not start_date:
        return {"empty": True, "error": "no_data", "message": "数据库中暂无快照数据",
                "tag_rollup": [], "cat_rollup": [], "opportunities": [],
                "new_entry": [], "pairs": [], "triples": [], "cross_platform": []}

    try:
        from market_analysis.data_access import connect_sqlite, load_rank_long_df
        from market_analysis.heat import HeatConfig, add_heat
        from market_analysis.metrics import (
            MetricConfig, add_unified_columns,
            compute_weekly_tag_panel, compute_timewindow_rollup,
            compute_weekly_category_panel,
            compute_timewindow_category_rollup,
            compute_new_entry_ratio_compact, compute_opening_opportunities,
            compute_cooccurrence_pairs, compute_cooccurrence_triples,
        )

        heat_cfg = HeatConfig(alpha=0.7, tanh_c=3.0)
        metric_cfg = MetricConfig(top_n_for_top_appearance=10, entry_top_n=30,
                                  top_k_tags=top_k, top_k_pairs=30, top_k_triples=30)

        conn = connect_sqlite(db_path)
        df = load_rank_long_df(conn, start_date=start_date, end_date=end_date, platform=platform)
        conn.close()

        if df is None or df.empty:
            return {"start_date": start_date, "end_date": end_date, "platform": platform,
                    "empty": True, "tag_rollup": [], "cat_rollup": [], "opportunities": [],
                    "new_entry": [], "pairs": [], "triples": [], "cross_platform": []}

        df = add_heat(df, heat_cfg)
        df = add_unified_columns(df)

        weekly = compute_weekly_tag_panel(df, metric_cfg)
        roll = compute_timewindow_rollup(weekly, metric_cfg)
        weekly_cat = compute_weekly_category_panel(df, metric_cfg)
        roll_cat = compute_timewindow_category_rollup(weekly_cat, metric_cfg)
        new_entry = compute_new_entry_ratio_compact(df, start_date, end_date)
        opportunities = compute_opening_opportunities(df, start_date, end_date)
        pairs = compute_cooccurrence_pairs(df, metric_cfg)
        triples = compute_cooccurrence_triples(df, metric_cfg)

        cross_platform = []
        if platform == "both" and roll_cat is not None and not roll_cat.empty:
            try:
                from market_analysis.report import build_cross_platform_diff_by_category
                cp_df = build_cross_platform_diff_by_category(roll_cat)
                if cp_df is not None and not cp_df.empty:
                    cp_df = cp_df.rename(columns={"cat_u": "category"})
                cross_platform = _safe_records(cp_df, 100)
            except Exception:
                pass
        if roll is not None and not roll.empty:
            roll = roll.rename(columns={"tag_u": "tag", "mean_share": "latest_share"})
        if roll_cat is not None and not roll_cat.empty:
            roll_cat = roll_cat.rename(columns={"cat_u": "category", "mean_share": "latest_share"})
        if opportunities is not None and not opportunities.empty:
            opportunities = opportunities.rename(columns={"tag_u": "tag", "cat_u": "category"})

        return {
            "start_date": start_date, "end_date": end_date, "platform": platform,
            "lookback": lookback, "top_k": top_k, "empty": False,
            "tag_rollup": _safe_records(roll.sort_values("avg_heat", ascending=False).head(top_k * 3)),
            "cat_rollup": _safe_records(roll_cat.sort_values("avg_heat", ascending=False).head(50) if roll_cat is not None else None),
            "opportunities": _safe_records(opportunities, 80),
            "new_entry": _safe_records(new_entry, 50),
            "pairs": _safe_records(pairs, 30),
            "triples": _safe_records(triples, 30),
            "cross_platform": cross_platform,
            # 真实 DB 小说计数（数量 / 新书占比的数据源，区别于榜单出现计数）
            "db_counts": _db_category_tag_counts(db_path, platform, start_date, end_date),
        }
    except ImportError as e:
        traceback.print_exc()
        raise RuntimeError(
            f"分析模块导入失败: {e}. 请确认 analysis/ 目录存在于项目根目录。"
        ) from e


@router.get("/run")
def run_analysis(
    platform: str = Query(default="both"),
    lookback: str = Query(default="all"),
    top_k: int = Query(default=20, ge=5, le=100),
    refresh: bool = Query(default=False),
    cached_only: bool = Query(default=False),
):
    """Instant-response trend analysis.

    Returns the compute_cache protocol envelope:
    ``{state:'ready', payload, stale, computing, updated_at}`` /
    ``{state:'computing'}`` / ``{state:'empty'}`` / ``{state:'error'}``.
    The heavy pipeline never runs in the request thread.
    """
    crawler_db = resolve_crawler_db_path()
    version = crawler_db_version()
    if not version:
        return {
            "state": "ready", "stale": False, "computing": False,
            "updated_at": None,
            "payload": {"empty": True, "error": "no_data",
                        "message": "未找到市场数据库文件",
                        "tag_rollup": [], "cat_rollup": [], "opportunities": [],
                        "new_entry": [], "pairs": [], "triples": [],
                        "cross_platform": []},
        }
    return compute_cache.get_or_compute(
        _project_db_path(),
        f"analysis_run:{platform}:{lookback}:{top_k}",
        version,
        lambda: _compute_analysis(crawler_db, platform, lookback, top_k),
        refresh=refresh,
        cached_only=cached_only,
    )