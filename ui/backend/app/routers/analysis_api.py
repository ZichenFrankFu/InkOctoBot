"""
/api/analysis — Wraps TrendAnalyzer for interactive frontend.
Handles import errors gracefully (analysis/ may not be installed).
"""
from __future__ import annotations
import sqlite3, sys, traceback, math
from datetime import date, timedelta
from pathlib import Path
from typing import Any
import numpy as np, pandas as pd
from fastapi import APIRouter, HTTPException, Query
from ..settings import settings
from ..utils import load_repo_config, get_db_path

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
    con = sqlite3.connect(db_path)
    r = pd.read_sql_query("SELECT MIN(snapshot_date) AS mn, MAX(snapshot_date) AS mx FROM rank_snapshots", con).iloc[0]
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
    repo_cfg = load_repo_config(settings.repo_root)
    db_path = get_db_path(repo_cfg, settings.repo_root)
    mn, mx = _db_date_range(db_path)
    return {"min_date": mn, "max_date": mx}

@router.get("/run")
def run_analysis(
    platform: str = Query(default="both"),
    lookback: str = Query(default="all"),
    top_k: int = Query(default=20, ge=5, le=100),
):
    # Ensure repo root is importable
    root = str(settings.repo_root)
    if root not in sys.path: sys.path.insert(0, root)

    repo_cfg = load_repo_config(settings.repo_root)
    db_path = get_db_path(repo_cfg, settings.repo_root)
    start_date, end_date = _compute_window(db_path, lookback)
    if not start_date:
        return {"empty": True, "error": "no_data", "message": "数据库中暂无快照数据",
                "tag_rollup": [], "cat_rollup": [], "opportunities": [],
                "new_entry": [], "pairs": [], "triples": [], "cross_platform": []}

    try:
        from analysis.data_access import connect_sqlite, load_rank_long_df
        from analysis.heat import HeatConfig, add_heat
        from analysis.metrics import (
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
                from analysis.report import build_cross_platform_diff_by_category
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
        }
    except ImportError as e:
        traceback.print_exc()
        raise HTTPException(500, f"分析模块导入失败: {e}. 请确认 analysis/ 目录存在于项目根目录。")
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(500, f"分析运行失败: {e}")