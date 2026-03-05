"""
/api/analysis — Exposes TrendAnalyzer results as JSON for the interactive frontend.

The heavy lifting is done by the existing analysis/ package. This router:
  1. accepts parameters (platform, lookback, top_k)
  2. runs TrendAnalyzer in-process (no subprocess)
  3. converts DataFrames → JSON-safe dicts
  4. returns a single large JSON blob for the frontend to render
"""
from __future__ import annotations

import sqlite3
import sys
import traceback
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from fastapi import APIRouter, HTTPException, Query

from ..settings import settings
from ..utils import load_repo_config, get_db_path

router = APIRouter(prefix="/analysis", tags=["analysis"])


# ──────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────

def _ensure_analysis_importable():
    """Ensure the repo root is on sys.path so `from analysis.xxx` works."""
    root = str(settings.repo_root)
    if root not in sys.path:
        sys.path.insert(0, root)


def _safe_records(df: pd.DataFrame | None, max_rows: int = 200) -> list[dict]:
    """Convert DataFrame to list-of-dicts, handling NaN → None."""
    if df is None or df.empty:
        return []
    d = df.head(max_rows).copy()
    # Replace NaN / inf with None for JSON serialisation
    d = d.replace([np.inf, -np.inf], np.nan)
    d = d.where(d.notna(), None)
    # Convert date/datetime columns
    for c in d.columns:
        if pd.api.types.is_datetime64_any_dtype(d[c]):
            d[c] = d[c].astype(str)
    return d.to_dict(orient="records")


def _db_date_range(db_path: str) -> tuple[str, str]:
    con = sqlite3.connect(db_path)
    row = pd.read_sql_query(
        "SELECT MIN(snapshot_date) AS mn, MAX(snapshot_date) AS mx FROM rank_snapshots", con
    ).iloc[0]
    con.close()
    mn = str(row["mn"]) if pd.notna(row["mn"]) else ""
    mx = str(row["mx"]) if pd.notna(row["mx"]) else ""
    return mn, mx


def _compute_window(db_path: str, lookback: str) -> tuple[str, str]:
    mn, mx = _db_date_range(db_path)
    if not mn:
        return "", ""
    end_d = date.today()
    if lookback == "all":
        start_d = pd.to_datetime(mn).date()
    else:
        days_map = {"week": 7, "month": 30, "quarter": 90, "year": 365}
        start_d = end_d - timedelta(days=days_map.get(lookback, 9999))
        db_min = pd.to_datetime(mn).date()
        if start_d < db_min:
            start_d = db_min
    return start_d.isoformat(), end_d.isoformat()


# ──────────────────────────────────────────────
# Endpoints
# ──────────────────────────────────────────────

@router.get("/date_range")
def analysis_date_range():
    """Return the min/max snapshot dates in the DB."""
    repo_cfg = load_repo_config(settings.repo_root)
    db_path = get_db_path(repo_cfg, settings.repo_root)
    mn, mx = _db_date_range(db_path)
    return {"min_date": mn, "max_date": mx}


@router.get("/run")
def run_analysis(
    platform: str = Query(default="both", regex="^(qidian|fanqie|both)$"),
    lookback: str = Query(default="all", regex="^(week|month|quarter|year|all)$"),
    top_k: int = Query(default=20, ge=5, le=100),
):
    """
    Run the full TrendAnalyzer pipeline and return structured JSON.

    This may take a few seconds for large databases.
    """
    _ensure_analysis_importable()

    repo_cfg = load_repo_config(settings.repo_root)
    db_path = get_db_path(repo_cfg, settings.repo_root)

    start_date, end_date = _compute_window(db_path, lookback)
    if not start_date:
        return {"error": "no_data", "message": "数据库中暂无快照数据"}

    try:
        from analysis.data_access import connect_sqlite, load_rank_long_df
        from analysis.heat import HeatConfig, add_heat
        from analysis.metrics import (
            MetricConfig,
            add_unified_columns,
            compute_weekly_tag_panel,
            compute_timewindow_rollup,
            compute_weekly_category_panel,
            compute_timewindow_category_rollup,
            compute_new_entry_ratio_compact,
            compute_opening_opportunities,
            compute_cooccurrence_pairs,
            compute_cooccurrence_triples,
        )
        from analysis.report import build_cross_platform_diff_by_category

        heat_cfg = HeatConfig(alpha=0.7, tanh_c=3.0)
        metric_cfg = MetricConfig(
            top_n_for_top_appearance=10,
            entry_top_n=30,
            top_k_tags=top_k,
            top_k_pairs=30,
            top_k_triples=30,
        )

        conn = connect_sqlite(db_path)
        df = load_rank_long_df(
            conn,
            start_date=start_date,
            end_date=end_date,
            platform=platform,
        )
        conn.close()

        if df is None or df.empty:
            return {
                "start_date": start_date,
                "end_date": end_date,
                "platform": platform,
                "empty": True,
                "tag_rollup": [],
                "cat_rollup": [],
                "opportunities": [],
                "new_entry": [],
                "pairs": [],
                "triples": [],
                "cross_platform": [],
            }

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

        # Cross-platform diff
        cross_platform: list[dict] = []
        if platform == "both" and roll_cat is not None and not roll_cat.empty:
            diff = build_cross_platform_diff_by_category(roll_cat)
            cross_platform = _safe_records(diff, 100)

        # Build response
        return {
            "start_date": start_date,
            "end_date": end_date,
            "platform": platform,
            "lookback": lookback,
            "top_k": top_k,
            "empty": False,
            "tag_rollup": _safe_records(
                roll.sort_values("avg_heat", ascending=False).head(top_k * 3)
            ),
            "cat_rollup": _safe_records(
                roll_cat.sort_values("avg_heat", ascending=False).head(50)
                if roll_cat is not None else None
            ),
            "opportunities": _safe_records(opportunities, 80),
            "new_entry": _safe_records(new_entry, 50),
            "pairs": _safe_records(pairs, 30),
            "triples": _safe_records(triples, 30),
            "cross_platform": cross_platform,
        }

    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Analysis failed: {str(e)}")
