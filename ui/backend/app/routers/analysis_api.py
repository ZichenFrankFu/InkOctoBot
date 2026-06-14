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
from fastapi import APIRouter, Body, HTTPException, Query
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


@router.get("/crawler-version")
def analysis_crawler_version():
    """市场数据库版本指纹（mtime+size）— 前端轮询，变化即自动刷新。"""
    return {"version": crawler_db_version() or ""}

def _window_pct(series: list[float]) -> float | None:
    """窗口内百分比变化 = (晚期均值 − 早期均值) / 早期均值。

    早期/晚期各取窗口 25% 的样本求均值（而非单点首尾），这样「全部数据」下
    早期个别快照覆盖不全（作品采集少→热度/数量结构性偏低）不会把涨幅放大成
    假高增长（武侠 +1038.9% 的根因）。短窗口退化为近似首尾对比。返回 None 表
    示无基线（单点或全零）。"""
    vals = [float(v) for v in series]
    pos = [v for v in vals if v > 0]
    if len(vals) < 2 or not pos:
        return None
    w = max(1, len(vals) // 4)
    head = [v for v in vals[:w] if v > 0] or pos[:w]
    tail = [v for v in vals[-w:] if v > 0] or pos[-w:]
    first = sum(head) / len(head)
    last = sum(tail) / len(tail)
    if first <= 0:
        return None
    return (last - first) / first


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
                # 趋势改为窗口内百分比变化（前端展示为 % 涨跌）。
                "count_pct": _window_pct(count_series),
                "heat_pct": _window_pct(heat_series),
                "share_pct": _window_pct(share_series),
                "new_count": int(new_counts.get(name, 0)),
            })
        return items

    cats = _build(cat_total, cat_heat, cat_nov, cat_new)
    tags = _build(tag_total, tag_heat, tag_nov, tag_new)

    # 起点: 按 taxonomy(config) 归类 — 大分类只留 novel_types，副分类只留
    # 已知子类并附上父级 大分类（前端据此标注「大分类·副分类」，不再硬编码）。
    if platform == "qidian":
        try:
            from ..services.market_extractor import taxonomy as _tax
            main_set = _tax.qidian_main_set()
            sub_to_main = _tax.qidian_sub_to_main()
            mf = [c for c in cats if c["name"] in main_set]
            sf = [t for t in tags if t["name"] in sub_to_main]
            if mf:
                cats = mf
            if sf:
                for t in sf:
                    t["parent"] = sub_to_main.get(t["name"], "")
                tags = sf
        except Exception:
            pass

    out["categories"] = cats
    out["tags"] = tags
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
        # v4: qidian panel filtered/classified server-side (parent on tags).
        f"analysis_run_v4:{platform}:{lookback}:{top_k}",
        version,
        lambda: _compute_analysis(crawler_db, platform, lookback, top_k),
        refresh=refresh,
        cached_only=cached_only,
    )


@router.get("/cache/stats")
def cache_stats():
    """缓存清单（条目数、字节、最旧/最新、在算任务数）— 统一缓存管理面板。"""
    return compute_cache.stats(_project_db_path())


@router.post("/cache/clear")
def cache_clear(prefix: str = Query(default="")):
    """清空缓存（全部或按 key 前缀，如 analysis_run / opening_nlp）。"""
    n = compute_cache.clear(_project_db_path(), prefix or None)
    return {"cleared": n, "prefix": prefix or "ALL"}


@router.post("/cache/evict-expired")
def cache_evict_expired():
    """手动触发 TTL 过期清理。"""
    return {"evicted": compute_cache.evict_expired(_project_db_path())}


# ── 词表资源管理 (人名 / 常用词) ── 「资源管理」tab CRUD + 高频词一键归类。
# 所有调用统一走 wordlists.py（resources/wordlists/*.txt + 用户 overlay）。

_WORDLIST_OK = {"surnames", "given_names", "common_words"}


def _wordlist_after_edit() -> None:
    """Hot-reload the NLP 词表 and drop the opening-NLP cache so the next
    基础特征提取 re-filters with the user's edit (无需改 crawler 数据版本)。"""
    try:
        from ..services.market_extractor import opening_stats as _os
        _os.reload_wordlists()
    except Exception:
        pass
    try:
        compute_cache.clear(_project_db_path(), "opening_nlp")
    except Exception:
        pass


@router.get("/wordlist")
def wordlist_list(list: str = Query(...), q: str = Query(default="")):
    """List 人名 / 常用词 资源（可搜索）。"""
    from ..services.market_extractor import wordlists as _wl
    if list not in _WORDLIST_OK:
        raise HTTPException(400, f"unknown list: {list!r}")
    data = _wl.list_words(list, q or None)
    data["list"] = list
    data["label"] = _wl.LIST_LABELS.get(list, list)
    return data


@router.get("/wordlist/names")
def wordlist_names(q: str = Query(default="")):
    """人名总览（按国家分组 + 姓/名）— 资源管理 tab 的人名视图。"""
    from ..services.market_extractor import wordlists as _wl
    return _wl.names_overview(q or None)


@router.get("/wordlist/grouped")
def wordlist_grouped(list: str = Query(...), q: str = Query(default="")):
    """分组列出资源（人名按国家分组 + 用户添加），供折叠展示。"""
    from ..services.market_extractor import wordlists as _wl
    if list not in _WORDLIST_OK:
        raise HTTPException(400, f"unknown list: {list!r}")
    data = _wl.list_grouped(list, q or None)
    data["label"] = _wl.LIST_LABELS.get(list, list)
    return data


@router.get("/wordlist/export")
def wordlist_export(list: str = Query(...)):
    """导出资源为 txt（每行一个词）。"""
    from fastapi.responses import PlainTextResponse
    from ..services.market_extractor import wordlists as _wl
    if list not in _WORDLIST_OK:
        raise HTTPException(400, f"unknown list: {list!r}")
    fname = f"{list}.txt"
    return PlainTextResponse(
        _wl.export_text(list),
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@router.post("/wordlist/import")
def wordlist_import(body: dict = Body(...)):
    """从 txt 内容批量导入资源（统一 txt 文档）。"""
    from ..services.market_extractor import wordlists as _wl
    lst = (body.get("list") or "").strip()
    content = body.get("content") or ""
    if lst not in _WORDLIST_OK:
        raise HTTPException(400, f"unknown list: {lst!r}")
    group = (body.get("group") or "").strip() or None
    added = _wl.import_text(lst, content, group)
    _wordlist_after_edit()
    return {"ok": True, "added": added, "list": lst}


@router.post("/wordlist/add")
def wordlist_add(body: dict = Body(...)):
    """归类某词为 人名 / 常用词（高频词右键归类 或 资源管理新增）。归类后该
    词在后续基础特征提取中不再被识别为高频词。"""
    from ..services.market_extractor import wordlists as _wl
    lst = (body.get("list") or "").strip()
    word = (body.get("word") or "").strip()
    if lst not in _WORDLIST_OK:
        raise HTTPException(400, f"unknown list: {lst!r}")
    if not word:
        raise HTTPException(400, "word is required")
    group = (body.get("group") or "").strip() or None   # 国别：中文/日本/西方
    added = _wl.add_word(lst, word, group)
    _wordlist_after_edit()
    return {"ok": True, "added": added, "word": word, "list": lst}


@router.post("/wordlist/remove")
def wordlist_remove(body: dict = Body(...)):
    """从 人名 / 常用词 资源删除某词。"""
    from ..services.market_extractor import wordlists as _wl
    lst = (body.get("list") or "").strip()
    word = (body.get("word") or "").strip()
    if lst not in _WORDLIST_OK:
        raise HTTPException(400, f"unknown list: {lst!r}")
    if not word:
        raise HTTPException(400, "word is required")
    removed = _wl.remove_word(lst, word)
    _wordlist_after_edit()
    return {"ok": True, "removed": removed, "word": word, "list": lst}