"""Market overview loader (spec loader 1, 开书助手 only).

Folds the same real data the user sees on the 市场特征提取 page (基础
特征 / 高级特征 tabs) into a compact digest the 开书助手 can quote.

Data sources, in priority order — every layer is real extracted data
that's already been computed somewhere; we never fabricate or hardcode
anything, and the heavy pandas pipeline is the LAST resort, not the
first:

  1. ``compute_cache`` rows under ``analysis_run_v4:*`` (trend analysis
     payload that ``/api/analysis/run`` writes when the user opens the
     基础特征提取 tab — tag rollup / category rollup / opportunities /
     co-occurrence pairs)
  2. ``compute_cache`` rows under ``opening_nlp:*`` (basic NLP cache
     written by ``/api/db/opening_nlp_analysis`` — sample size, word
     count distribution, dialogue ratio, sentence types)
  3. ``category_aggregated_stats`` rows (高级特征提取 Phase-4 aggregate)
  4. Direct SQL on the crawler DB — book count, main_category breakdown,
     top tags. Always available when the 市场数据库 file exists.
  5. As a last resort (when none of the above produced anything), the
     legacy pandas pipeline that recomputes the trend rollup from
     scratch.

Returns ``None`` when none of the layers produced content.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from typing import Any

from ..budget_allocator import LOADER_BUDGETS
from ..loader_protocol import LoaderPlan, make_plan
from ..utils import clip

logger = logging.getLogger("inkoctobot.services.prompt_context.market_overview")


_BLOCK = "market_overview"
_TITLE = "市场总览（来自 市场特征提取）"

# Per-subsection cap so a single noisy field can't blow the budget.
_CAP_PER_SUBSECTION = 360
# Top-N rendered per distribution (tags / categories / pairs / etc.).
_TOP_N_TAGS = 8
_TOP_N_CATEGORIES = 6
_TOP_N_OPPORTUNITIES = 6
_TOP_N_PAIRS = 6


# ─────────── helpers ───────────


def _project_db_path() -> str:
    try:
        from ui.backend.app.services.project_paths import get_db_path
        return get_db_path()
    except Exception as e:
        logger.debug("project db path lookup failed: %s", e)
        return ""


def _crawler_db_path() -> str:
    try:
        from ui.backend.app.utils import resolve_crawler_db_path
        from pathlib import Path
        p = resolve_crawler_db_path()
        if p and Path(p).exists():
            return p
    except Exception as e:
        logger.debug("crawler db path lookup failed: %s", e)
    return ""


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


def _safe_json(raw: Any) -> Any:
    if raw is None:
        return None
    if isinstance(raw, (dict, list)):
        return raw
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None


# ─────────── source #1 — analysis_run_v4 trend cache ───────────


def _latest_trend_cache(db_path: str) -> dict | None:
    """Return the freshest ``analysis_run_v4:*`` payload — the basic-
    features tab's primary signal. Empty payloads (``empty=True``) are
    skipped so we don't render a noisy "no data" digest.
    """
    if not db_path:
        return None
    try:
        with sqlite3.connect(db_path) as con:
            rows = con.execute(
                "SELECT payload_json, updated_at FROM compute_cache "
                "WHERE cache_key LIKE 'analysis_run_v4:%' "
                "ORDER BY updated_at DESC LIMIT 5"
            ).fetchall()
    except sqlite3.OperationalError:
        return None
    for raw, _ts in rows:
        data = _safe_json(raw)
        if isinstance(data, dict) and not data.get("empty"):
            return data
    return None


def _render_trend_section(trend: dict) -> list[str]:
    parts: list[str] = []
    start = str(trend.get("start_date") or "")
    end = str(trend.get("end_date") or "")
    head = "市场趋势"
    if start and end:
        head += f"（{start} → {end}）"
    parts.append(head + "：")

    # Top tags
    tags = trend.get("tag_rollup") or []
    if isinstance(tags, list) and tags:
        bits: list[str] = []
        for t in tags[:_TOP_N_TAGS]:
            if not isinstance(t, dict):
                continue
            name = str(t.get("tag") or t.get("tag_u") or "").strip()
            heat = t.get("avg_heat") or t.get("latest_share")
            if not name:
                continue
            bits.append(f"{name}({_fmt_num(heat)})" if heat is not None else name)
        if bits:
            parts.append(clip("- 热门标签：" + "、".join(bits), _CAP_PER_SUBSECTION))

    # Top categories
    cats = trend.get("cat_rollup") or []
    if isinstance(cats, list) and cats:
        bits = []
        for c in cats[:_TOP_N_CATEGORIES]:
            if not isinstance(c, dict):
                continue
            name = str(c.get("category") or c.get("cat_u") or "").strip()
            share = c.get("latest_share") or c.get("mean_share")
            heat = c.get("avg_heat")
            if not name:
                continue
            tag = name
            if share is not None:
                tag += f"(份额{_fmt_pct(share)}"
                if heat is not None:
                    tag += f", 热度{_fmt_num(heat)}"
                tag += ")"
            bits.append(tag)
        if bits:
            parts.append(clip("- 主流分类：" + "、".join(bits), _CAP_PER_SUBSECTION))

    # Opportunities
    opps = trend.get("opportunities") or []
    if isinstance(opps, list) and opps:
        bits = []
        for o in opps[:_TOP_N_OPPORTUNITIES]:
            if not isinstance(o, dict):
                continue
            tag = str(o.get("tag") or o.get("tag_u") or o.get("category") or "").strip()
            score = o.get("opportunity_score")
            new_ratio = o.get("new_entry_ratio")
            if not tag:
                continue
            line = tag
            if score is not None:
                line += f"(机会分{_fmt_num(score)}"
                if new_ratio is not None:
                    line += f", 新书占比{_fmt_pct(new_ratio)}"
                line += ")"
            bits.append(line)
        if bits:
            parts.append(clip("- 开书机会：" + "、".join(bits), _CAP_PER_SUBSECTION))

    # Co-occurring tag pairs
    pairs = trend.get("pairs") or []
    if isinstance(pairs, list) and pairs:
        bits = []
        for p in pairs[:_TOP_N_PAIRS]:
            if not isinstance(p, dict):
                continue
            a = str(p.get("tag_a") or p.get("a") or "").strip()
            b = str(p.get("tag_b") or p.get("b") or "").strip()
            if a and b:
                bits.append(f"{a}+{b}")
        if bits:
            parts.append(clip("- 高频标签组合：" + "、".join(bits), _CAP_PER_SUBSECTION))

    return parts


# ─────────── source #2 — opening_nlp cache ───────────


def _latest_opening_nlp_cache(db_path: str) -> dict | None:
    """Return the freshest ``opening_nlp:*`` payload (basic-features tab
    NLP analysis on the first chapters).
    """
    if not db_path:
        return None
    try:
        with sqlite3.connect(db_path) as con:
            rows = con.execute(
                "SELECT payload_json FROM compute_cache "
                "WHERE cache_key LIKE 'opening_nlp:%' "
                "ORDER BY updated_at DESC LIMIT 5"
            ).fetchall()
    except sqlite3.OperationalError:
        return None
    for (raw,) in rows:
        data = _safe_json(raw)
        if isinstance(data, dict) and data.get("available"):
            return data
    return None


def _render_opening_nlp_section(nlp: dict) -> list[str]:
    parts: list[str] = []
    sample = int(nlp.get("sample_count") or 0)
    novels = int(nlp.get("unique_novels") or 0)
    parts.append(f"开篇 NLP（{novels} 部 / {sample} 个样本）：")
    wcs = nlp.get("word_count_summary") or {}
    if isinstance(wcs, dict) and wcs.get("mean"):
        parts.append(
            f"- 开篇字数 均 {int(wcs.get('mean') or 0)} "
            f"(min {int(wcs.get('min') or 0)} / max {int(wcs.get('max') or 0)})"
        )
    dr = nlp.get("dialogue_ratio") or {}
    if isinstance(dr, dict) and dr.get("mean") is not None:
        parts.append(f"- 开篇对话占比 均 {_fmt_pct(dr.get('mean'))}")
    sl = nlp.get("sentence_length") or {}
    if isinstance(sl, dict) and sl.get("mean"):
        parts.append(f"- 开篇平均句长 {float(sl.get('mean') or 0):.1f} 字")
    for field, label in [
        ("first_sentence_types", "首句类型"),
        ("end_hook_types",       "尾钩类型"),
    ]:
        items = nlp.get(field) or []
        if isinstance(items, list) and items:
            top = []
            for x in items[:_TOP_N_TAGS]:
                if isinstance(x, dict) and x.get("label"):
                    top.append(f"{x.get('label')} {x.get('count')}")
            if top:
                parts.append(clip(f"- {label}：" + " · ".join(top), _CAP_PER_SUBSECTION))
    return parts


# ─────────── source #3 — category_aggregated_stats table ───────────


def _load_aggregated_stats(db_path: str) -> list[dict]:
    """Latest ``category_aggregated_stats`` rows across platforms (advanced
    tab Phase 4). One row per (platform, category) — return the most
    recent by aggregated_at, up to 6.
    """
    if not db_path:
        return []
    try:
        with sqlite3.connect(db_path) as con:
            con.row_factory = sqlite3.Row
            rows = con.execute(
                "SELECT * FROM category_aggregated_stats "
                "ORDER BY aggregated_at DESC LIMIT 6"
            ).fetchall()
        return [dict(r) for r in rows]
    except sqlite3.OperationalError:
        return []


def _render_aggregated_stats_section(rows: list[dict]) -> list[str]:
    parts: list[str] = []
    parts.append(f"高级特征提取结果（{len(rows)} 个 (平台·分类) 组合）：")
    for r in rows:
        plat = str(r.get("platform") or "?")
        cat = str(r.get("category") or "")
        works = int(r.get("source_works_count") or 0)
        head = f"- {plat}"
        if cat:
            head += f" · {cat}"
        head += f"（{works} 部）"
        bits: list[str] = []
        hook = _safe_json(r.get("opening_hook_type_distribution_json"))
        if isinstance(hook, dict) and hook:
            top = sorted(hook.items(), key=lambda kv: -int(kv[1] or 0))[:3]
            bits.append("开篇钩子 " + " · ".join(f"{k}{v}" for k, v in top))
        cheat = _safe_json(r.get("protagonist_cheat_type_distribution_json"))
        if isinstance(cheat, dict) and cheat:
            top = sorted(cheat.items(), key=lambda kv: -int(kv[1] or 0))[:3]
            bits.append("金手指 " + " · ".join(f"{k}{v}" for k, v in top))
        wcs = _safe_json(r.get("chapter_word_count_stats_json"))
        if isinstance(wcs, dict) and wcs.get("p50"):
            bits.append(f"章均 {int(wcs.get('p50') or 0)} 字")
        if bits:
            head += "：" + "；".join(bits)
        parts.append(clip(head, _CAP_PER_SUBSECTION))
    return parts


# ─────────── source #4 — crawler DB direct SQL ───────────


def _load_crawler_direct(crawler_db: str) -> dict:
    """Cheap aggregate queries on the 市场数据库 — runs on every loader
    call (no cache) since the queries are O(seconds) and the crawler DB
    file is local. Mirrors what the 市场特征提取 tabs visualize so the
    digest stays in sync with what the user is seeing.
    """
    if not crawler_db:
        return {}
    out: dict[str, Any] = {}
    try:
        with sqlite3.connect(crawler_db) as con:
            con.row_factory = sqlite3.Row
            # Platform-wide book count and average word count.
            try:
                rows = con.execute(
                    "SELECT platform, COUNT(*) AS n, AVG(total_words) AS avg_w "
                    "FROM novels WHERE platform IS NOT NULL AND platform != '' "
                    "GROUP BY platform ORDER BY n DESC LIMIT 8"
                ).fetchall()
                out["platforms"] = [
                    {"platform": r["platform"], "count": int(r["n"]),
                     "avg_words": int(r["avg_w"] or 0)}
                    for r in rows if r["platform"]
                ]
            except sqlite3.OperationalError:
                out["platforms"] = []
            # Top main categories across the crawler corpus.
            try:
                rows = con.execute(
                    "SELECT main_category AS k, COUNT(*) AS n FROM novels "
                    "WHERE main_category IS NOT NULL AND main_category != '' "
                    "GROUP BY main_category ORDER BY n DESC LIMIT 10"
                ).fetchall()
                out["main_categories"] = [
                    {"category": r["k"], "count": int(r["n"])}
                    for r in rows if r["k"]
                ]
            except sqlite3.OperationalError:
                out["main_categories"] = []
            # Top tags across the crawler corpus.
            try:
                rows = con.execute(
                    "SELECT t.tag_name AS tag, COUNT(*) AS n "
                    "FROM tags t JOIN novel_tag_map m ON m.tag_id = t.tag_id "
                    "GROUP BY t.tag_name ORDER BY n DESC LIMIT 15"
                ).fetchall()
                out["top_tags"] = [
                    {"tag": r["tag"], "count": int(r["n"])}
                    for r in rows if r["tag"]
                ]
            except sqlite3.OperationalError:
                out["top_tags"] = []
    except sqlite3.OperationalError as e:
        logger.debug("crawler DB direct aggregate failed: %s", e)
        return {}
    return out


def _render_crawler_direct_section(agg: dict) -> list[str]:
    parts: list[str] = []
    platforms = agg.get("platforms") or []
    if platforms:
        bits = [
            f"{p['platform']}({p['count']} 部, 均 {p['avg_words']:,} 字)"
            for p in platforms
        ]
        parts.append(clip("市场基底 · 各平台规模：" + "、".join(bits), _CAP_PER_SUBSECTION))
    cats = agg.get("main_categories") or []
    if cats:
        bits = [f"{c['category']} {c['count']}" for c in cats]
        parts.append(clip("- 主分类分布：" + "、".join(bits), _CAP_PER_SUBSECTION))
    tags = agg.get("top_tags") or []
    if tags:
        bits = [f"{t['tag']} {t['count']}" for t in tags]
        parts.append(clip("- 全库高频标签：" + "、".join(bits), _CAP_PER_SUBSECTION))
    return parts


# ─────────── source #5 — legacy pandas digest (last resort) ───────────


# Single-process cache for the legacy heavy path so we don't re-run pandas
# on every plan() call when nothing else is available.
_legacy_cache_key: tuple[str, int] | None = None
_legacy_cache_digest: str = ""


def _legacy_pandas_digest(crawler_db: str) -> str:
    """The pre-refactor heavy pipeline. Only invoked when all faster
    sources are empty AND the ``market_analysis`` package is importable.
    Cached by (path, mtime_ns) so repeat calls are free.
    """
    global _legacy_cache_key, _legacy_cache_digest
    if not crawler_db:
        return ""
    from pathlib import Path
    p = Path(crawler_db)
    if not p.exists():
        return ""
    key = (str(p), p.stat().st_mtime_ns)
    if key == _legacy_cache_key:
        return _legacy_cache_digest

    try:
        from market_analysis.data_access import connect_sqlite, load_rank_long_df
        from market_analysis.heat import HeatConfig, add_heat
        from market_analysis.metrics import (
            MetricConfig, add_unified_columns,
            compute_weekly_category_panel,
            compute_timewindow_category_rollup,
            compute_opening_opportunities,
            compute_cooccurrence_pairs,
        )
    except ImportError as e:
        logger.debug("market_analysis import failed (legacy path skipped): %s", e)
        _legacy_cache_key, _legacy_cache_digest = key, ""
        return ""

    try:
        conn = connect_sqlite(str(p))
        try:
            df = load_rank_long_df(conn, platform="both")
        finally:
            conn.close()
        if df is None or df.empty:
            _legacy_cache_key, _legacy_cache_digest = key, ""
            return ""
        df = add_heat(df, HeatConfig(alpha=0.7, tanh_c=3.0))
        df = add_unified_columns(df)
        cfg = MetricConfig()
        lines: list[str] = []
        try:
            weekly_cat = compute_weekly_category_panel(df, cfg)
            roll_cat = compute_timewindow_category_rollup(weekly_cat, cfg)
            if roll_cat is not None and not roll_cat.empty:
                agg = (
                    roll_cat.groupby("cat_u", dropna=True)
                    .agg(avg_heat=("avg_heat", "mean"),
                         mean_share=("mean_share", "mean"))
                    .reset_index()
                    .sort_values("avg_heat", ascending=False)
                    .head(8)
                )
                bits = [
                    f"{r['cat_u']}(份额{_fmt_pct(r['mean_share'])}, 热度{_fmt_num(r['avg_heat'])})"
                    for _, r in agg.iterrows()
                ]
                if bits:
                    lines.append(clip("主要类目：" + "、".join(bits), _CAP_PER_SUBSECTION))
        except Exception:
            pass
        try:
            start = str(df["snapshot_date"].min())[:10]
            end = str(df["snapshot_date"].max())[:10]
            opp = compute_opening_opportunities(df, start, end)
            if opp is not None and not opp.empty:
                bits = []
                for _, r in opp.head(6).iterrows():
                    tag = r.get("tag_u") or r.get("tag") or r.get("cat_u") or "-"
                    bits.append(f"{tag}(机会{_fmt_num(r.get('opportunity_score'))})")
                if bits:
                    lines.append(clip("开书机会：" + "、".join(bits), _CAP_PER_SUBSECTION))
        except Exception:
            pass
        try:
            pairs = compute_cooccurrence_pairs(df, cfg)
            if pairs is not None and not pairs.empty:
                bits = []
                for _, r in pairs.head(6).iterrows():
                    a = r.get("tag_a") or r.get("a") or ""
                    b = r.get("tag_b") or r.get("b") or ""
                    if a and b:
                        bits.append(f"{a}+{b}")
                if bits:
                    lines.append(clip("高频标签组合：" + "、".join(bits), _CAP_PER_SUBSECTION))
        except Exception:
            pass
        digest = "\n".join(lines).strip()
        _legacy_cache_key, _legacy_cache_digest = key, digest
        return digest
    except Exception as e:
        logger.debug("legacy pandas digest failed: %s", e)
        _legacy_cache_key, _legacy_cache_digest = key, ""
        return ""


# ─────────── assembly ───────────


def _build_body(project_db: str, crawler_db: str) -> str:
    """Walk the 5 sources in priority order, concatenating whatever each
    one returns. Same-tier sections are joined with a blank line; per-
    section caps keep any one source from dominating the budget."""
    sections: list[str] = []

    trend = _latest_trend_cache(project_db)
    if trend:
        rendered = _render_trend_section(trend)
        if rendered:
            sections.append("\n".join(rendered))

    nlp = _latest_opening_nlp_cache(project_db)
    if nlp:
        rendered = _render_opening_nlp_section(nlp)
        if rendered:
            sections.append("\n".join(rendered))

    stats = _load_aggregated_stats(project_db)
    if stats:
        rendered = _render_aggregated_stats_section(stats)
        if rendered:
            sections.append("\n".join(rendered))

    crawler_agg = _load_crawler_direct(crawler_db)
    if crawler_agg:
        rendered = _render_crawler_direct_section(crawler_agg)
        if rendered:
            sections.append("\n".join(rendered))

    if not sections:
        # Last resort — pandas pipeline. Already skips silently when
        # market_analysis can't import.
        legacy = _legacy_pandas_digest(crawler_db)
        if legacy:
            sections.append(legacy)

    return "\n\n".join(sections).strip()


def plan(project_id: str, exclude: set | None = None) -> LoaderPlan | None:
    body = _build_body(_project_db_path(), _crawler_db_path())
    if not body:
        return None
    return make_plan(_BLOCK, _TITLE, body)


def load(project_id: str, exclude: set | None = None) -> str:
    p = plan(project_id, exclude)
    return p.render(p.target) if p else ""


def diagnose() -> dict:
    """Step-by-step report: which source had content, sample size of each.
    Mounted at ``/api/generation/diagnose/market-overview`` so users can
    see exactly what the loader pulled in.
    """
    project_db = _project_db_path()
    crawler_db = _crawler_db_path()
    trend = _latest_trend_cache(project_db)
    nlp = _latest_opening_nlp_cache(project_db)
    stats = _load_aggregated_stats(project_db)
    crawler = _load_crawler_direct(crawler_db)
    body = _build_body(project_db, crawler_db)
    return {
        "project_db_path":  project_db,
        "crawler_db_path":  crawler_db or "(not configured / file missing)",
        "sources": {
            "analysis_run_v4_cache": {
                "present": bool(trend),
                "tag_rollup_n": len((trend or {}).get("tag_rollup") or []),
                "cat_rollup_n": len((trend or {}).get("cat_rollup") or []),
                "opportunities_n": len((trend or {}).get("opportunities") or []),
                "pairs_n": len((trend or {}).get("pairs") or []),
            },
            "opening_nlp_cache": {
                "present": bool(nlp),
                "sample_count": (nlp or {}).get("sample_count"),
                "unique_novels": (nlp or {}).get("unique_novels"),
            },
            "category_aggregated_stats": {
                "present": bool(stats),
                "rows": len(stats),
            },
            "crawler_db_direct": {
                "present": bool(crawler),
                "platforms_n": len(crawler.get("platforms") or []),
                "main_categories_n": len(crawler.get("main_categories") or []),
                "top_tags_n": len(crawler.get("top_tags") or []),
            },
        },
        "rendered_length": len(body),
        "rendered_preview": body[:800],
    }
