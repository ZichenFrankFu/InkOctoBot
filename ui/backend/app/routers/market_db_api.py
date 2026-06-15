from __future__ import annotations
import logging
import sqlite3
from pathlib import Path
from fastapi import APIRouter, HTTPException, Query, Body
from pydantic import BaseModel
from ..settings import settings
from ..utils import load_repo_config, get_crawler_db_path

router = APIRouter(prefix="/db", tags=["db"])
logger = logging.getLogger("inkoctobot.ui.backend.db_api")

def _get_con() -> sqlite3.Connection | None:
    """Return a DB connection, or *None* when the crawler DB is unavailable.

    Uses the project-wide ``resolve_crawler_db_path()`` so the same
    user-configured path (Settings page → 市场数据库 path) takes effect
    here and in ``analysis_api`` and ``marketing_api``.
    """
    from ..utils import resolve_crawler_db_path

    db_path = resolve_crawler_db_path()
    if not db_path or not Path(db_path).exists():
        logger.info("Crawler DB not found at %s — returning empty data", db_path)
        return None
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    return con

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
        recent_sql = "SELECT s.snapshot_id,s.rank_list_id,s.snapshot_date,s.item_count,l.platform,l.rank_family,l.rank_sub_cat FROM rank_snapshots s JOIN rank_lists l ON l.rank_list_id=s.rank_list_id" + (" WHERE l.platform=?" if platform else "") + " ORDER BY s.snapshot_date DESC LIMIT 20"
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

@router.get("/search_novels")
def search_novels(
    q: str = Query(..., description="title / author / intro search query"),
    platform: str | None = None,
    limit: int = Query(default=30, ge=1, le=100),
):
    """Search novels by title (primary or alias), author, or intro substring.

    Returns rows matching ANY of: novel_titles.title LIKE %q% OR
    novels.author LIKE %q% OR novels.intro LIKE %q%, deduplicated by
    novel_uid, with the primary title joined for display.
    """
    con = _get_con()
    if con is None:
        return {"items": [], "total": 0}
    pat = f"%{q.strip()}%"
    params: list[Any] = [pat, pat, pat]
    plat_clause = ""
    if platform:
        plat_clause = " AND n.platform = ?"
        params.append(platform)
    sql = (
        "SELECT DISTINCT n.novel_uid, n.platform, n.author, n.main_category, "
        "n.status, n.total_words, n.last_seen_date, "
        "(SELECT title FROM novel_titles t WHERE t.novel_uid=n.novel_uid "
        " ORDER BY t.is_primary DESC, t.last_seen_date DESC LIMIT 1) AS title "
        "FROM novels n "
        "LEFT JOIN novel_titles t ON t.novel_uid = n.novel_uid "
        "WHERE (t.title LIKE ? OR n.author LIKE ? OR n.intro LIKE ?)"
        + plat_clause +
        " ORDER BY n.last_seen_date DESC LIMIT ?"
    )
    params.append(limit)
    with con:
        if not _table_exists(con, "novels"):
            return {"items": [], "total": 0}
        rows = con.execute(sql, params).fetchall()
    return {"items": [dict(r) for r in rows], "total": len(rows)}


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

@router.get("/chapter/{chapter_id}")
def chapter_content(chapter_id: int):
    """Return the full content of one ``first_n_chapters`` row.

    Separate endpoint so the novel_detail endpoint stays light
    (it ships only metadata for the chapter list). Frontend fetches
    the body on-demand when the user expands a chapter.
    """
    con = _get_con()
    if con is None:
        raise HTTPException(404, "Crawler DB not available")
    with con:
        if not _table_exists(con, "first_n_chapters"):
            raise HTTPException(404, "chapters not available")
        r = con.execute(
            "SELECT chapter_id, novel_uid, chapter_num, chapter_title, "
            "chapter_content, chapter_url, word_count, publish_date "
            "FROM first_n_chapters WHERE chapter_id = ?",
            (chapter_id,),
        ).fetchone()
    if not r:
        raise HTTPException(404, "chapter not found")
    return dict(r)


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

@router.get("/market_brief")
def market_brief(platform: str | None = None):
    """Concise market-data summary text for grounding the AI 开书助手."""
    con = _get_con()
    if con is None:
        return {"brief": ""}
    with con:
        if not _table_exists(con, "novels"):
            return {"brief": ""}
        pp = [platform] if platform else []
        pw = " WHERE platform=?" if platform else ""
        novel_count = con.execute(f"SELECT COUNT(*) AS c FROM novels{pw}", pp).fetchone()["c"]
        cats = con.execute(
            "SELECT main_category, COUNT(*) AS count FROM novels" + pw
            + " GROUP BY main_category ORDER BY count DESC LIMIT 10", pp,
        ).fetchall()
        tag_sql = ("SELECT t.tag_name, COUNT(DISTINCT m.novel_uid) AS c "
                   "FROM tags t JOIN novel_tag_map m ON m.tag_id=t.tag_id")
        tp: list = []
        if platform:
            tag_sql += " JOIN novels n ON n.novel_uid=m.novel_uid WHERE n.platform=?"
            tp.append(platform)
        tag_sql += " GROUP BY t.tag_name ORDER BY c DESC LIMIT 20"
        tags = con.execute(tag_sql, tp).fetchall()
        tn = []
        if _table_exists(con, "rank_entries"):
            tnw = " WHERE l.platform=?" if platform else ""
            tnp: list = ([platform] if platform else []) + [12]
            tn = con.execute(
                "SELECT nt.title, n.main_category, COUNT(DISTINCT e.snapshot_id) AS apps "
                "FROM rank_entries e JOIN rank_snapshots s ON s.snapshot_id=e.snapshot_id "
                "JOIN rank_lists l ON l.rank_list_id=s.rank_list_id "
                "JOIN novels n ON n.novel_uid=e.novel_uid "
                "LEFT JOIN novel_titles nt ON nt.novel_uid=n.novel_uid AND nt.is_primary=1"
                + tnw + " GROUP BY n.novel_uid ORDER BY apps DESC LIMIT ?", tnp,
            ).fetchall()
    parts = [f"市场数据库共收录 {novel_count} 部作品。"]
    if cats:
        parts.append("热门分类（按作品数）："
                      + "、".join(f"{r['main_category']}({r['count']})" for r in cats if r["main_category"]))
    if tags:
        parts.append("高频题材标签：" + "、".join(f"{r['tag_name']}({r['c']})" for r in tags))
    if tn:
        parts.append("近期上榜热门作品：" + "、".join(f"《{r['title']}》" for r in tn if r["title"]))
    return {"brief": "\n".join(parts)}


@router.get("/opening_analysis")
def opening_analysis(platform: str | None = None):
    """Aggregate stats on the crawled opening chapters (first_n_chapters)."""
    con = _get_con()
    if con is None:
        return {"available": False}
    with con:
        if not _table_exists(con, "first_n_chapters"):
            return {"available": False}
        use_plat = bool(platform) and _table_exists(con, "novels")
        frm = "first_n_chapters fc"
        cond = ""
        params: list = []
        if use_plat:
            frm += " JOIN novels n ON n.novel_uid=fc.novel_uid"
            cond = " WHERE n.platform=?"
            params = [platform]
        novels_with = con.execute(
            f"SELECT COUNT(DISTINCT fc.novel_uid) c FROM {frm}{cond}", params).fetchone()["c"]
        total_ch = con.execute(
            f"SELECT COUNT(*) c FROM {frm}{cond}", params).fetchone()["c"]
        w_cond = (cond + " AND" if cond else " WHERE") + " fc.chapter_num=1 AND fc.word_count > 0"
        rows = con.execute(
            f"SELECT fc.word_count w FROM {frm}{w_cond}", params).fetchall()
    words = sorted(int(r["w"]) for r in rows if r["w"])
    n = len(words)
    buckets = {"<1000": 0, "1000–2000": 0, "2000–3000": 0, "3000–4000": 0, "≥4000": 0}
    for w in words:
        if w < 1000: buckets["<1000"] += 1
        elif w < 2000: buckets["1000–2000"] += 1
        elif w < 3000: buckets["2000–3000"] += 1
        elif w < 4000: buckets["3000–4000"] += 1
        else: buckets["≥4000"] += 1
    return {
        "available": True,
        "novels_with_chapters": novels_with,
        "total_chapters": total_ch,
        "first_chapter": {
            "count": n,
            "avg_words": round(sum(words) / n) if n else 0,
            "median_words": words[n // 2] if n else 0,
            "min_words": words[0] if n else 0,
            "max_words": words[-1] if n else 0,
            "distribution": buckets,
        },
    }


def _compute_opening_nlp(platform: str | None = None) -> dict:
    """Heuristic NLP analysis on the crawled first chapters.

    Counting + PMI over up to 600 chapters takes long enough that it
    must NOT run in the request thread — it only ever executes on a
    compute_cache background thread (see ``opening_nlp_analysis``).
    """
    import re
    con = _get_con()
    if con is None:
        return {"available": False, "reason": "crawler DB not configured"}
    with con:
        if not _table_exists(con, "first_n_chapters"):
            return {"available": False, "reason": "no first_n_chapters table"}
        frm = "first_n_chapters fc"
        cond = " WHERE fc.chapter_content IS NOT NULL AND length(fc.chapter_content) > 300"
        params: list = []
        if platform and _table_exists(con, "novels"):
            frm += " JOIN novels n ON n.novel_uid=fc.novel_uid"
            cond += " AND n.platform=?"
            params.append(platform)
        # 作品名 (primary title) 随章节带出 → 支撑「点击高频词→使用最多的
        # top3 作品 + 片段」的下钻。
        has_titles = _table_exists(con, "novel_titles")
        if has_titles:
            frm += (" LEFT JOIN novel_titles nt ON nt.novel_uid=fc.novel_uid "
                    "AND nt.is_primary=1")
        title_expr = "nt.title" if has_titles else "NULL"
        all_rows = con.execute(
            f"SELECT fc.chapter_num cn, fc.chapter_content cc, fc.word_count wc, "
            f"fc.novel_uid uid, {title_expr} title "
            f"FROM {frm}{cond} ORDER BY fc.novel_uid, fc.chapter_num LIMIT 600",
            params,
        ).fetchall()
        rows = [r for r in all_rows if int(r["cn"] or 0) == 1][:200]
        # Accurate corpus totals (not capped by the LIMIT above) for the
        # 基础特征提取 概览行：涉及多少 unique 小说、共多少章。
        cnt_frm = "first_n_chapters fc"
        cnt_cond = ""
        cnt_params: list = []
        if platform and _table_exists(con, "novels"):
            cnt_frm += " JOIN novels n ON n.novel_uid=fc.novel_uid"
            cnt_cond = " WHERE n.platform=?"
            cnt_params = [platform]
        totals = con.execute(
            f"SELECT COUNT(DISTINCT fc.novel_uid) AS nv, COUNT(*) AS ch "
            f"FROM {cnt_frm}{cnt_cond}", cnt_params,
        ).fetchone()
        unique_novels = int(totals["nv"] or 0)
        total_chapters = int(totals["ch"] or 0)
    if not rows:
        return {"available": False, "reason": "no openings to analyze",
                "unique_novels": unique_novels, "total_chapters": total_chapters}

    # Quote chars used in mainland web fiction.
    QUOTE_RE = re.compile(r"[「][^」]{1,400}[」]|[“][^”]{1,400}[”]|[\"][^\"]{1,400}[\"]")
    SENT_SPLIT_RE = re.compile(r"[。！？!?…]+")

    def first_sentence_type(text: str) -> str:
        t = text.strip()[:300]
        if not t:
            return "未知"
        # Pull the first sentence (up to the first 。！？).
        m = SENT_SPLIT_RE.split(t, maxsplit=1)
        first = (m[0] if m else t)[:160]
        if QUOTE_RE.search(first):
            return "对白开场"
        if re.search(r"[挥砍劈刺撞冲扑奔跑跃飞掷炸轰击撕扯踢踹]", first):
            return "动作开场"
        if re.search(r"年|月|日|时|公元|纪元|世纪", first):
            return "时间地点铺陈"
        if re.search(r"我|他|她|它|此刻|忽然|突然", first):
            return "人物心理/动作"
        if re.search(r"在.+(里|中|上|下)|城|山|村|庙|殿|宫|林", first):
            return "环境/世界观"
        return "叙事铺陈"

    def end_hook_type(text: str) -> str:
        tail = text.strip()[-300:]
        if not tail:
            return "未知"
        if QUOTE_RE.search(tail):
            return "对白收尾"
        if re.search(r"[？?]\s*$", tail):
            return "悬疑提问"
        if re.search(r"[！!]\s*$", tail):
            return "冲突爆发"
        if re.search(r"[…]{2,}\s*$|\.{3,}\s*$", tail):
            return "悬念省略"
        if re.search(r"看到|发现|出现|睁开|想起|意识到", tail[-100:]):
            return "悬念揭示"
        return "平稳过渡"

    dialogue_ratios: list[float] = []
    sentence_lengths: list[float] = []
    first_types: dict[str, int] = {}
    end_types: dict[str, int] = {}
    word_counts: list[int] = []

    for r in rows:
        text = str(r["cc"] or "")
        if not text:
            continue
        total_len = len(text)
        word_counts.append(int(r["wc"] or total_len))
        # dialogue ratio
        dialogue_chars = sum(len(m.group(0)) for m in QUOTE_RE.finditer(text))
        dialogue_ratios.append(round(dialogue_chars / total_len, 4) if total_len else 0)
        # sentence lengths
        sents = [s.strip() for s in SENT_SPLIT_RE.split(text) if s.strip()]
        if sents:
            sentence_lengths.append(sum(len(s) for s in sents) / len(sents))
        # categorical
        ft = first_sentence_type(text)
        first_types[ft] = first_types.get(ft, 0) + 1
        et = end_hook_type(text)
        end_types[et] = end_types.get(et, 0) + 1

    def _mean(xs: list[float]) -> float:
        return round(sum(xs) / len(xs), 3) if xs else 0.0

    def _hist(xs: list[float], buckets: list[tuple[str, float, float]]) -> dict:
        out = {label: 0 for label, _, _ in buckets}
        for x in xs:
            for label, lo, hi in buckets:
                if lo <= x < hi:
                    out[label] += 1
                    break
        return out

    # spec 2.1.3.2 §1 开篇章节NLP分析 维度（首章/章均/中位字数、平均
    # 句长、分类型标点密度、高频词、生造词Step1）— shared helper so the
    # 启动提取 prompt injects the SAME numbers.
    from ..services.market_extractor.opening_stats import compute_opening_stats
    from ..services.project_paths import get_db_path as _gdp
    # 每天一次：打开基础特征提取时自动触发人名库刷新（后台、不阻塞本次分析）。
    try:
        from ..services.market_extractor import name_refresh as _nr
        from ..utils import resolve_crawler_db_path as _rcdp
        _nr.maybe_daily_refresh(_gdp(), _rcdp(), platform=platform)
    except Exception as _e:
        logger.debug("daily name refresh skipped: %s", _e)
    spec_stats = compute_opening_stats([
        {"chapter_num": int(r["cn"] or 0), "text": str(r["cc"] or ""),
         "title": (r["title"] or f"作品#{r['uid']}")}
        for r in all_rows
    ], db_path=_gdp())

    return {
        "available": True,
        "platform": platform or "all",
        "sample_count": len(rows),
        "unique_novels": unique_novels,
        "total_chapters": total_chapters,
        "spec_stats": spec_stats,
        "dialogue_ratio": {
            "mean": _mean(dialogue_ratios),
            "distribution": _hist(dialogue_ratios, [
                ("0-10%", 0.0, 0.10),
                ("10-25%", 0.10, 0.25),
                ("25-40%", 0.25, 0.40),
                (">=40%", 0.40, 9.99),
            ]),
        },
        "sentence_length": {
            "mean": _mean(sentence_lengths),
            "distribution": _hist(sentence_lengths, [
                ("≤15字", 0.0, 15.0),
                ("15-25字", 15.0, 25.0),
                ("25-40字", 25.0, 40.0),
                (">40字", 40.0, 9999.0),
            ]),
        },
        "first_sentence_types":
            sorted([{"label": k, "count": v} for k, v in first_types.items()],
                   key=lambda d: -d["count"]),
        "end_hook_types":
            sorted([{"label": k, "count": v} for k, v in end_types.items()],
                   key=lambda d: -d["count"]),
        "word_count_summary": {
            "mean": int(sum(word_counts) / len(word_counts)) if word_counts else 0,
            "min": min(word_counts) if word_counts else 0,
            "max": max(word_counts) if word_counts else 0,
        },
    }


@router.get("/opening_nlp_analysis")
def opening_nlp_analysis(
    platform: str | None = None,
    refresh: bool = Query(default=False),
    cached_only: bool = Query(default=False),
) -> dict:
    """开篇章节NLP分析, served through the persistent compute cache.

    Instant response: last finished result (``stale`` flag when the
    crawler DB changed since) or ``{state:'computing'}``. ``cached_only``
    is the 懒加载 path used on page mount — it never starts the heavy
    job, only ``refresh`` (or a first poll without ``cached_only``) does.
    """
    from ..services import compute_cache
    from ..services.project_paths import get_db_path
    from ..utils import crawler_db_version

    version = crawler_db_version()
    if not version:
        return {
            "state": "ready", "stale": False, "computing": False,
            "updated_at": None,
            "payload": {"available": False, "reason": "crawler DB not configured"},
        }
    return compute_cache.get_or_compute(
        get_db_path(),
        f"opening_nlp:{platform or 'all'}",
        version,
        lambda: _compute_opening_nlp(platform),
        refresh=refresh,
        cached_only=cached_only,
    )


@router.post("/opening_ai_summary")
async def opening_ai_summary(body: dict = Body(...)):
    """AI analysis of the crawled opening-chapter content. ``prompt_only``
    returns the assembled prompt for the AI大模型网页版 copy-paste flow."""
    platform = body.get("platform") or None
    prompt_only = bool(body.get("prompt_only"))
    con = _get_con()
    if con is None or not _table_exists(con, "first_n_chapters"):
        raise HTTPException(404, "暂无已采集的开篇章节")
    with con:
        has_titles = _table_exists(con, "novel_titles")
        title_expr = ("COALESCE(nt.title, fc.chapter_title, '佚名')"
                      if has_titles else "COALESCE(fc.chapter_title, '佚名')")
        frm = "first_n_chapters fc"
        if has_titles:
            frm += " LEFT JOIN novel_titles nt ON nt.novel_uid=fc.novel_uid AND nt.is_primary=1"
        cond = (" WHERE fc.chapter_num=1 AND fc.chapter_content IS NOT NULL "
                "AND length(fc.chapter_content) > 300")
        params: list = []
        if platform and _table_exists(con, "novels"):
            frm += " JOIN novels n ON n.novel_uid=fc.novel_uid"
            cond += " AND n.platform=?"
            params.append(platform)
        rows = con.execute(
            f"SELECT {title_expr} t, fc.chapter_content cc FROM {frm}{cond} LIMIT 14",
            params,
        ).fetchall()
    if not rows:
        raise HTTPException(404, "暂无可分析的开篇章节正文")
    samples = []
    for r in rows:
        cc = str(r["cc"] or "").strip().replace("\r", "")
        if len(cc) > 1200:
            cc = cc[:1200] + "……"
        samples.append(f"《{r['t']}》\n{cc}")
    prompt = (
        f"以下是 {len(rows)} 部网文（{platform or '全部平台'}）的开篇第一章节选。"
        "请作为资深网文编辑分析这些开篇，用中文输出总结：\n"
        "1. 常见开篇方式（高潮开局 / 对话开局 / 世界观铺陈 / 人物登场 等）及倾向占比；\n"
        "2. 抓读者的核心手法：钩子、悬念、爽点如何设置；\n"
        "3. 开篇的节奏与信息密度特点；\n"
        "4. 共性规律与可直接借鉴的开篇技巧建议。\n\n"
        "【开篇样本】\n" + "\n\n———\n\n".join(samples)
    )
    if prompt_only:
        return {"prompt": prompt, "sample_count": len(rows)}
    try:
        from llm.router import ModelRouter

        summary = await ModelRouter().invoke(
            role="reference_extractor", prompt=prompt,
            max_tokens=1800, temperature=0.5,
        )
    except Exception as e:
        raise HTTPException(502, f"AI 总结调用失败：{e}")
    return {"summary": str(summary or "").strip(), "sample_count": len(rows)}


# ─────────── novel CRUD (manual correction surface) ───────────


_EDITABLE_NOVEL_COLS = {
    "author", "intro", "main_category", "status", "total_words", "url",
}


@router.put("/novel/{novel_uid}")
def update_novel(novel_uid: int, body: dict = Body(...)):
    """Editable subset of the novels row for manual correction. Only the
    fields in ``_EDITABLE_NOVEL_COLS`` are accepted (plus ``title`` —
    primary novel_titles row — and ``tags`` as a full replacement
    list); everything else is ignored to avoid the UI shaping
    crawler-derived identity columns.

    Crawler concurrency (spec 市场数据库·机制4): the write connection
    queues behind an in-flight crawler transaction via busy_timeout +
    BEGIN IMMEDIATE; a still-held lock surfaces as 423."""
    patches = {k: v for k, v in body.items() if k in _EDITABLE_NOVEL_COLS}
    title = (body.get("title") or "").strip() if "title" in body else ""
    tags = body.get("tags") if isinstance(body.get("tags"), list) else None
    if not patches and not title and tags is None:
        raise HTTPException(
            400,
            f"no editable fields. allowed: {sorted(_EDITABLE_NOVEL_COLS)} + title/tags",
        )
    con = _get_write_con()
    if con is None:
        raise HTTPException(404, "Crawler DB not available")
    try:
        con.execute("BEGIN IMMEDIATE")
        row = con.execute(
            "SELECT novel_uid FROM novels WHERE novel_uid=?",
            (novel_uid,),
        ).fetchone()
        if row is None:
            con.rollback()
            raise HTTPException(404, "novel not found")
        if patches:
            sets = ", ".join(f"{k}=?" for k in patches)
            params = list(patches.values()) + [novel_uid]
            con.execute(f"UPDATE novels SET {sets} WHERE novel_uid=?", params)
        if title:
            updated_title = con.execute(
                "UPDATE novel_titles SET title = ?, title_norm = ? "
                "WHERE novel_uid = ? AND is_primary = 1",
                (title, _norm(title), novel_uid),
            )
            if updated_title.rowcount == 0:
                con.execute(
                    "INSERT INTO novel_titles (novel_uid, title, title_norm, "
                    "is_primary, first_seen_date, last_seen_date) "
                    "VALUES (?, ?, ?, 1, date('now'), date('now'))",
                    (novel_uid, title, _norm(title)),
                )
            patches["title"] = title
        if tags is not None:
            con.execute(
                "DELETE FROM novel_tag_map WHERE novel_uid = ?", (novel_uid,),
            )
            for tag in tags:
                tag = str(tag).strip()
                if not tag:
                    continue
                con.execute(
                    "INSERT OR IGNORE INTO tags (tag_name, tag_norm) "
                    "VALUES (?, ?)",
                    (tag, _norm(tag)),
                )
                tag_id = con.execute(
                    "SELECT tag_id FROM tags WHERE tag_norm = ?",
                    (_norm(tag),),
                ).fetchone()[0]
                con.execute(
                    "INSERT OR IGNORE INTO novel_tag_map (novel_uid, tag_id) "
                    "VALUES (?, ?)",
                    (novel_uid, tag_id),
                )
            patches["tags"] = tags
        con.commit()
        updated = dict(con.execute(
            "SELECT * FROM novels WHERE novel_uid=?", (novel_uid,),
        ).fetchone())
    except sqlite3.OperationalError as e:
        con.rollback()
        if "locked" in str(e).lower() or "busy" in str(e).lower():
            raise HTTPException(
                423, "市场数据库正被爬虫写入，已等待 30 秒仍未释放，请稍后重试",
            )
        raise HTTPException(500, f"update failed: {e}")
    finally:
        con.close()
    return {"novel": updated, "patched": list(patches.keys())}


@router.delete("/novel/{novel_uid}")
def delete_novel(novel_uid: int):
    """Hard-delete a novel and all dependent crawler rows (titles, tags,
    chapters, rank-entry rows). For the manual-correction surface — use
    carefully."""
    con = _get_con()
    if con is None:
        raise HTTPException(404, "Crawler DB not available")
    with con:
        row = con.execute(
            "SELECT novel_uid FROM novels WHERE novel_uid=?",
            (novel_uid,),
        ).fetchone()
        if row is None:
            raise HTTPException(404, "novel not found")
        for stmt in [
            "DELETE FROM rank_entries WHERE novel_uid=?",
            "DELETE FROM novel_tag_map WHERE novel_uid=?",
            "DELETE FROM novel_titles WHERE novel_uid=?",
            "DELETE FROM first_n_chapters WHERE novel_uid=?",
            "DELETE FROM novels WHERE novel_uid=?",
        ]:
            try:
                con.execute(stmt, (novel_uid,))
            except sqlite3.OperationalError:
                pass
        con.commit()
    return {"ok": True, "novel_uid": novel_uid}


class _NewNovelBody(BaseModel):
    platform: str
    platform_novel_id: str = ""
    author: str = ""
    main_category: str = ""
    intro: str = ""
    status: str = "ongoing"
    total_words: int = 0
    url: str = ""
    title: str = ""


@router.post("/novel")
def create_novel(body: _NewNovelBody):
    """Insert a manually-curated novel row + a primary title (the UI
    needs a way to fix up the data set when a crawler missed something)."""
    con = _get_con()
    if con is None:
        raise HTTPException(404, "Crawler DB not available")
    if not body.platform.strip():
        raise HTTPException(400, "platform required")
    with con:
        cur = con.execute(
            """INSERT INTO novels
               (platform, platform_novel_id, author, intro, main_category,
                status, total_words, url)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (body.platform.strip(), body.platform_novel_id.strip(),
             body.author.strip(), body.intro.strip(),
             body.main_category.strip(), body.status.strip(),
             int(body.total_words or 0), body.url.strip()),
        )
        novel_uid = cur.lastrowid
        if body.title.strip():
            from datetime import date as _date
            con.execute(
                "INSERT INTO novel_titles (novel_uid, title, is_primary, "
                "first_seen_date, last_seen_date) VALUES (?, ?, 1, ?, ?)",
                (novel_uid, body.title.strip(),
                 _date.today().isoformat(), _date.today().isoformat()),
            )
        con.commit()
    return {"novel_uid": novel_uid}


@router.get("/db-mtime")
def db_mtime():
    """Return the crawler DB's mtime so the UI can lazily skip
    reloading the home-page summary when nothing has changed."""
    from ..utils import resolve_crawler_db_path
    db_path = resolve_crawler_db_path()
    if not db_path or not Path(db_path).exists():
        return {"mtime": 0, "exists": False}
    try:
        mtime = int(Path(db_path).stat().st_mtime)
        size = Path(db_path).stat().st_size
        return {"mtime": mtime, "size": size, "exists": True}
    except OSError:
        return {"mtime": 0, "exists": False}


@router.get("/info")
def db_info():
    """Where the market DB actually resolves to + which precedence layer
    won (user override / test mode / paths.yaml default). Surfaced in the
    Settings page so the user can see whether their custom path took
    effect.
    """
    import json
    from ..utils import resolve_crawler_db_path

    db_path = resolve_crawler_db_path()
    available = bool(db_path) and Path(db_path).exists()

    # Diagnose which source won so the UI can show "user override active"
    # vs "fell back to default".
    source = "default"
    user_set = ""
    try:
        sp = settings.get_data_path("settings.json")
        if sp.exists():
            user_set = (json.loads(sp.read_text("utf-8")).get("crawler_db_path") or "").strip()
            if user_set and Path(user_set).exists() and Path(user_set).resolve() == Path(db_path).resolve():
                source = "user_override"
            elif user_set:
                source = "user_override_invalid"
    except Exception:
        pass
    if source == "default" and settings.test_mode:
        source = "test_mode"
    return {
        "db_path": db_path,
        "available": available,
        "test_mode": settings.test_mode,
        "source": source,
        "user_set_path": user_set,
    }

@router.get("/tables")
def list_tables():
    con = _get_con()
    if con is None:
        return {"tables": []}
    with con:
        return {"tables": [r["name"] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()]}


# ──────────────────────────────────────────────────────────────
# v3.1 UPDATE endpoints (spec 市场数据库·机制4/机制5)
#
# Users may READ & UPDATE market data (basic novel info + collected
# chapter text) — never INSERT/DELETE, the market DB must keep
# reflecting the real market. Concurrency with the external crawler
# (失败措施·机制5): writes open the connection with a 30s
# busy_timeout and BEGIN IMMEDIATE, so a user write QUEUES behind any
# in-flight crawler transaction instead of failing; if the crawler
# holds the lock longer than that we surface 423 (locked) so the UI
# can tell the user to retry after the crawl finishes.
# ──────────────────────────────────────────────────────────────


_WRITE_BUSY_TIMEOUT_MS = 30_000

# Whitelist of user-editable novels columns (机制5: only basic info).
_NOVEL_EDITABLE = {
    "author", "intro", "main_category", "status", "total_words",
    "url", "created_date",
}


def _get_write_con() -> sqlite3.Connection | None:
    from ..utils import resolve_crawler_db_path
    db_path = resolve_crawler_db_path()
    if not db_path or not Path(db_path).exists():
        return None
    con = sqlite3.connect(db_path, timeout=_WRITE_BUSY_TIMEOUT_MS / 1000)
    con.row_factory = sqlite3.Row
    con.execute(f"PRAGMA busy_timeout = {_WRITE_BUSY_TIMEOUT_MS}")
    return con


def _norm(s: str) -> str:
    return "".join((s or "").lower().split())


@router.put("/chapter/{chapter_id}")
def update_chapter(chapter_id: int, body: dict = Body(...)):
    """Update one collected chapter's title / content (机制5: UPDATE only)."""
    title = body.get("chapter_title")
    content = body.get("chapter_content")
    if title is None and content is None:
        raise HTTPException(400, "nothing to update")
    con = _get_write_con()
    if con is None:
        raise HTTPException(404, "Crawler DB not available")
    try:
        con.execute("BEGIN IMMEDIATE")
        sets, args = [], []
        if title is not None:
            sets.append("chapter_title = ?")
            args.append(str(title))
        if content is not None:
            sets.append("chapter_content = ?")
            args.append(str(content))
            sets.append("word_count = ?")
            args.append(len(str(content)))
        cur = con.execute(
            f"UPDATE first_n_chapters SET {', '.join(sets)} "
            f"WHERE chapter_id = ?",
            (*args, chapter_id),
        )
        if cur.rowcount == 0:
            con.rollback()
            raise HTTPException(404, "chapter not found")
        con.commit()
    except sqlite3.OperationalError as e:
        con.rollback()
        if "locked" in str(e).lower() or "busy" in str(e).lower():
            raise HTTPException(
                423, "市场数据库正被爬虫写入，已等待 30 秒仍未释放，请稍后重试",
            )
        raise HTTPException(500, f"update failed: {e}")
    finally:
        con.close()
    return {"ok": True, "chapter_id": chapter_id}
