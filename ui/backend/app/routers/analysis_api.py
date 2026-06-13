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

def _linear_slope(ys: list[float]) -> float | None:
    """Least-squares slope of ``ys`` against its index. None for <2 points."""
    n = len(ys)
    if n < 2:
        return None
    mx = (n - 1) / 2.0
    my = sum(ys) / n
    denom = sum((i - mx) ** 2 for i in range(n))
    if denom == 0:
        return None
    return sum((i - mx) * (ys[i] - my) for i in range(n)) / denom


def _basic_market_panel(db_path: str, platform: str,
                        start_date: str, end_date: str) -> dict:
    """Per-大分类(main_category) and per-副分类/标签(tag) panel computed
    DIRECTLY from the DB — robust to the market_analysis cat_u/tag_u
    normalization that didn't match the stored names (the cause of 副分类
    热度/份额 暂无 and trends showing 「-」).

    Each item: name, total(库内小说数), heat(最新快照该分类下所有作品热度之
    和), share(热度占比), count/heat/share 的时间斜率, new_count(出现在新书
    榜的作品数 — 新书统一定义为「来自新书榜」)。
    """
    out: dict = {"categories": [], "tags": []}
    if not Path(db_path).exists():
        return out
    plat_ok = platform in ("qidian", "fanqie")
    pcond = "l.platform = ? AND " if plat_ok else ""
    pp: list = [platform] if plat_ok else []
    npp: list = [platform] if plat_ok else []
    nfilter = " WHERE platform = ?" if plat_ok else ""
    tfilter = " WHERE n.platform = ?" if plat_ok else ""
    try:
        con = sqlite3.connect(db_path)
        # (date, novel) heat — one value per novel per day (recommend/reading).
        rows = con.execute(
            f"""SELECT s.snapshot_date AS d, e.novel_uid AS uid,
                       MAX(COALESCE(e.total_recommend, e.reading_count, 0)) AS heat
                FROM rank_entries e
                JOIN rank_snapshots s ON s.snapshot_id = e.snapshot_id
                JOIN rank_lists l ON l.rank_list_id = s.rank_list_id
                WHERE {pcond} s.snapshot_date BETWEEN ? AND ?
                GROUP BY s.snapshot_date, e.novel_uid""",
            pp + [start_date, end_date],
        ).fetchall()
        catmap: dict[int, str] = {}
        for uid, cat in con.execute(
                f"SELECT novel_uid, main_category FROM novels{nfilter}", npp):
            catmap[uid] = cat or ""
        tagmap: dict[int, list[str]] = {}
        for uid, tag in con.execute(
                "SELECT m.novel_uid, t.tag_name FROM novel_tag_map m "
                "JOIN tags t ON t.tag_id = m.tag_id "
                f"JOIN novels n ON n.novel_uid = m.novel_uid{tfilter}", npp):
            if tag:
                tagmap.setdefault(uid, []).append(tag)
        newset: set[int] = set()
        for (uid,) in con.execute(
                f"""SELECT DISTINCT e.novel_uid FROM rank_entries e
                    JOIN rank_snapshots s ON s.snapshot_id = e.snapshot_id
                    JOIN rank_lists l ON l.rank_list_id = s.rank_list_id
                    WHERE {pcond} l.rank_family LIKE '%新书%'
                      AND s.snapshot_date BETWEEN ? AND ?""",
                pp + [start_date, end_date]):
            newset.add(uid)
        cat_total: dict[str, int] = {}
        for cat, c in con.execute(
                f"SELECT main_category, COUNT(*) FROM novels{nfilter} "
                "GROUP BY main_category", npp):
            if cat:
                cat_total[cat] = int(c)
        tag_total: dict[str, int] = {}
        for tag, c in con.execute(
                "SELECT t.tag_name, COUNT(DISTINCT m.novel_uid) "
                "FROM novel_tag_map m JOIN tags t ON t.tag_id = m.tag_id "
                f"JOIN novels n ON n.novel_uid = m.novel_uid{tfilter} "
                "GROUP BY t.tag_name", npp):
            if tag:
                tag_total[tag] = int(c)
        con.close()
    except sqlite3.OperationalError:
        return out

    from collections import defaultdict
    dates = sorted({r[0] for r in rows})
    cat_heat: dict = defaultdict(lambda: defaultdict(float))
    cat_nov: dict = defaultdict(lambda: defaultdict(set))
    tag_heat: dict = defaultdict(lambda: defaultdict(float))
    tag_nov: dict = defaultdict(lambda: defaultdict(set))
    date_total: dict = defaultdict(float)
    for d, uid, heat in rows:
        heat = heat or 0
        date_total[d] += heat
        cat = catmap.get(uid, "")
        if cat:
            cat_heat[d][cat] += heat
            cat_nov[d][cat].add(uid)
        for tg in tagmap.get(uid, []):
            tag_heat[d][tg] += heat
            tag_nov[d][tg].add(uid)

    cat_new: dict[str, int] = defaultdict(int)
    tag_new: dict[str, int] = defaultdict(int)
    for uid in newset:
        cat = catmap.get(uid)
        if cat:
            cat_new[cat] += 1
        for tg in tagmap.get(uid, []):
            tag_new[tg] += 1

    def _build(totals, date_heat, date_nov, new_counts):
        items = []
        for name, total in totals.items():
            heat_series = [date_heat[d].get(name, 0.0) for d in dates]
            count_series = [float(len(date_nov[d].get(name, ()))) for d in dates]
            share_series = [
                (date_heat[d].get(name, 0.0) / date_total[d]) if date_total[d] else 0.0
                for d in dates
            ]
            items.append({
                "name": name,
                "total": int(total),
                "avg_heat": round(heat_series[-1]) if heat_series else 0,
                "latest_share": round(share_series[-1], 4) if share_series else 0.0,
                "count_slope": _linear_slope(count_series),
                "heat_slope": _linear_slope(heat_series),
                "share_slope": _linear_slope(share_series),
                "new_count": int(new_counts.get(name, 0)),
            })
        return items

    out["categories"] = _build(cat_total, cat_heat, cat_nov, cat_new)
    out["tags"] = _build(tag_total, tag_heat, tag_nov, tag_new)
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
            # 大分类/副分类面板：数量 + 热度 + 份额 + 各自趋势 + 新书数，
            # 直接由 DB 计算（数量=库内小说数，热度=该分类作品热度之和）。
            "panel": _basic_market_panel(db_path, platform, start_date, end_date),
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
        # v2: payload now carries the DB-computed `panel` (was `db_counts`).
        f"analysis_run_v2:{platform}:{lookback}:{top_k}",
        version,
        lambda: _compute_analysis(crawler_db, platform, lookback, top_k),
        refresh=refresh,
        cached_only=cached_only,
    )