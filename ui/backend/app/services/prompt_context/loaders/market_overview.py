"""Market overview loader (spec loader 1, 开书助手 only).

Folds the market_analysis commercial rollup (各分类作品数量 / 热度 /
份额 / 趋势 / 开书机会 / 标签共现) into a compact digest block. Reads
the crawler DB through the same ``market_analysis`` functions the
``/api/analysis/run`` endpoint uses — no LLM call at prompt-build time.

The pandas pipeline is too heavy to re-run on every prompt build, so
the digest is cached per (db_path, db_mtime): it only recomputes when
the crawler DB file actually changes.

Returns ``None`` when the crawler DB is missing/empty or the analysis
package is unavailable.
"""
from __future__ import annotations

import logging
from pathlib import Path

from ..loader_protocol import LoaderPlan, make_plan

logger = logging.getLogger("inkoctobot.services.prompt_context.market_overview")


_BLOCK = "market_overview"
_TITLE = "市场总览（商业分析）"

# digest cache — keyed by (db_path, mtime_ns); single-entry is enough
# because there is one crawler DB per install.
_cache_key: tuple[str, int] | None = None
_cache_digest: str = ""


def _fmt_pct(v) -> str:
    try:
        return f"{float(v) * 100:.1f}%"
    except (TypeError, ValueError):
        return "-"


def _fmt_num(v) -> str:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return "-"
    return f"{f:.2f}" if abs(f) < 100 else f"{f:,.0f}"


def _compute_digest(db_path: str) -> str:
    from market_analysis.data_access import connect_sqlite, load_rank_long_df
    from market_analysis.heat import HeatConfig, add_heat
    from market_analysis.metrics import (
        MetricConfig, add_unified_columns,
        compute_weekly_category_panel, compute_timewindow_category_rollup,
        compute_opening_opportunities, compute_cooccurrence_pairs,
    )

    conn = connect_sqlite(db_path)
    try:
        df = load_rank_long_df(conn, platform="both")
    finally:
        conn.close()
    if df is None or df.empty:
        return ""

    df = add_heat(df, HeatConfig(alpha=0.7, tanh_c=3.0))
    df = add_unified_columns(df)
    cfg = MetricConfig()

    lines: list[str] = []

    # 各分类: 数量 / 热度 / 份额 / 份额趋势
    try:
        weekly_cat = compute_weekly_category_panel(df, cfg)
        roll_cat = compute_timewindow_category_rollup(weekly_cat, cfg)
        if roll_cat is not None and not roll_cat.empty:
            agg = (
                roll_cat.groupby("cat_u", dropna=True)
                .agg(avg_heat=("avg_heat", "mean"),
                     mean_share=("mean_share", "mean"),
                     share_slope=("share_slope", "mean"))
                .reset_index()
                .sort_values("avg_heat", ascending=False)
                .head(12)
            )
            counts = df.groupby("cat_u")["novel_uid"].nunique()
            lines.append("主要类目（按热度排序）：")
            for _, r in agg.iterrows():
                cat = r["cat_u"]
                slope = r["share_slope"]
                trend = (
                    "上升" if isinstance(slope, (int, float)) and slope > 0
                    else "下降" if isinstance(slope, (int, float)) and slope < 0
                    else "平稳"
                )
                lines.append(
                    f"- {cat}: 作品数{int(counts.get(cat, 0))} "
                    f"热度{_fmt_num(r['avg_heat'])} "
                    f"份额{_fmt_pct(r['mean_share'])} "
                    f"份额趋势{trend}"
                )
    except Exception as e:
        logger.debug("market_overview category rollup skipped: %s", e)

    # 开书机会
    try:
        start = str(df["snapshot_date"].min())[:10]
        end = str(df["snapshot_date"].max())[:10]
        opp = compute_opening_opportunities(df, start, end)
        if opp is not None and not opp.empty:
            lines.append("开书机会（份额/热度增长 + 新书占比综合）：")
            for _, r in opp.head(8).iterrows():
                tag = r.get("tag_u") or r.get("tag") or r.get("cat_u") or "-"
                lines.append(
                    f"- {tag}: 机会分{_fmt_num(r.get('opportunity_score'))} "
                    f"新书占比{_fmt_pct(r.get('new_entry_ratio'))}"
                )
    except Exception as e:
        logger.debug("market_overview opportunities skipped: %s", e)

    # 标签共现
    try:
        pairs = compute_cooccurrence_pairs(df, cfg)
        if pairs is not None and not pairs.empty:
            top_pairs = []
            for _, r in pairs.head(8).iterrows():
                a = r.get("tag_a") or r.get("a") or ""
                b = r.get("tag_b") or r.get("b") or ""
                if a and b:
                    top_pairs.append(f"{a}+{b}")
            if top_pairs:
                lines.append("高频标签组合：" + "、".join(top_pairs))
    except Exception as e:
        logger.debug("market_overview cooccurrence skipped: %s", e)

    return "\n".join(lines).strip()


def _digest() -> str:
    global _cache_key, _cache_digest
    try:
        from ui.backend.app.utils import resolve_crawler_db_path
        db_path = resolve_crawler_db_path()
    except Exception:
        return ""
    p = Path(db_path)
    if not p.exists():
        return ""
    key = (str(p), p.stat().st_mtime_ns)
    if key == _cache_key:
        return _cache_digest
    try:
        digest = _compute_digest(str(p))
    except Exception as e:
        logger.debug("market_overview digest failed: %s", e)
        return ""
    _cache_key, _cache_digest = key, digest
    return digest


def plan(project_id: str, exclude: set | None = None) -> LoaderPlan | None:
    body = _digest()
    if not body:
        return None
    return make_plan(_BLOCK, _TITLE, body)


def load(project_id: str, exclude: set | None = None) -> str:
    p = plan(project_id, exclude)
    return p.render(p.target) if p else ""
