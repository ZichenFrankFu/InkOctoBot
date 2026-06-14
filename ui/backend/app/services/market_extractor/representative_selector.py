"""Phase 1: pick representative works per (platform, category).

Reads from the crawler DB — callers should pass ``crawler_db``
explicitly (the canonical ``resolve_crawler_db_path()``); otherwise
``_resolve_crawler_db()`` falls back to WN_CRAWLER_DB env → canonical
resolver → ``data/InkOctoBot_Crawler.db``. Scores each novel, selects
top 30, randomly tags 10% as holdout, persists into
``representative_works_pool``.

Tolerates a missing crawler DB: returns 0 selections rather than
crashing — the pipeline-job manager surfaces this as a notification.
"""
from __future__ import annotations

import logging
import os
import random
import sqlite3
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger("inkoctobot.market_extractor.representative_selector")


_DEFAULT_TOP_K = 30
_HOLDOUT_FRACTION = 0.1
_MIN_WORD_COUNT = 200_000
# Bonus / penalty weights for the composite rank score.
_WEIGHTS = {
    "collections":   0.4,
    "comments":      0.2,
    "monthly_ticket": 0.3,
    "word_count":    0.1,
}


@dataclass
class WorkCandidate:
    novel_id: str
    title: str
    platform: str
    category: str
    collections: int
    comments: int
    monthly_ticket: int
    word_count: int
    last_updated_at: str | None
    rank_score: float = 0.0
    selection_reason: str = ""


def _resolve_crawler_db() -> str:
    """Crawler DB path. Honors ``WN_CRAWLER_DB`` env override, then the
    app-wide ``resolve_crawler_db_path()`` (Settings-page override /
    test mode / paths.yaml) — the selector silently reading a DIFFERENT
    file than every other market endpoint was the 「暂无候选代表作」
    bug. Last resort: ``data/InkOctoBot_Crawler.db``."""
    env = os.environ.get("WN_CRAWLER_DB")
    if env:
        return env
    try:
        from ui.backend.app.utils import resolve_crawler_db_path
        p = resolve_crawler_db_path()
        if p:
            return p
    except Exception as e:
        logger.debug("canonical crawler path resolution failed: %s", e)
    repo_root = Path(__file__).resolve().parents[5]
    return str(repo_root / "data" / "InkOctoBot_Crawler.db")


def _fetch_board_candidates(
    crawler_db: str, platform: str, category: str,
) -> list[WorkCandidate]:
    """真实数据选取 (spec 市场数据库特征提取·机制1/机制2) against this
    repo's market schema (storage/market_schema.py):

    1. 高 weight 榜单 + 高 rank — every rank_list matching the selected
       榜单 (``rank_sub_cat == category`` or ``rank_family == category``)
       contributes its LATEST snapshot's entries, scored by rank
       position + platform heat (起点 total_recommend / 番茄
       reading_count).
    2. 随机抽取新书榜作品 — a few random entries from the platform's
       新书 boards join the pool regardless of heat.
    """
    out: dict[str, WorkCandidate] = {}
    try:
        with sqlite3.connect(crawler_db) as con:
            con.row_factory = sqlite3.Row

            def _latest_entries(where: str, params: list) -> list[sqlite3.Row]:
                return con.execute(
                    f"""
                    SELECT e.novel_uid, e.rank, e.total_recommend,
                           e.reading_count,
                           n.total_words, n.last_seen_date,
                           l.rank_family, l.rank_sub_cat
                    FROM rank_lists l
                    JOIN rank_snapshots s ON s.rank_list_id = l.rank_list_id
                     AND s.snapshot_date = (
                         SELECT MAX(s2.snapshot_date) FROM rank_snapshots s2
                         WHERE s2.rank_list_id = l.rank_list_id)
                    JOIN rank_entries e ON e.snapshot_id = s.snapshot_id
                    JOIN novels n ON n.novel_uid = e.novel_uid
                    WHERE {where}
                    ORDER BY e.rank ASC
                    """,
                    params,
                ).fetchall()

            # ① 所选榜单（高 weight + 高 rank）
            board_rows = _latest_entries(
                "l.platform = ? AND (l.rank_sub_cat = ? OR l.rank_family = ?)",
                [platform, category, category],
            )
            heats = [
                int(r["total_recommend"] or 0) + int(r["reading_count"] or 0)
                for r in board_rows
            ]
            n_heat = _normalize(heats)
            max_rank = max((int(r["rank"] or 1) for r in board_rows), default=1)
            for i, r in enumerate(board_rows):
                uid = str(r["novel_uid"])
                rank_pos = 1.0 - (int(r["rank"] or 1) - 1) / max(1, max_rank)
                score = 0.6 * rank_pos + 0.4 * n_heat[i]
                cand = out.get(uid)
                if cand is None or score > cand.rank_score:
                    c = WorkCandidate(
                        novel_id=uid, title="", platform=platform,
                        category=category,
                        collections=int(r["total_recommend"] or 0),
                        comments=0,
                        monthly_ticket=int(r["reading_count"] or 0),
                        word_count=int(r["total_words"] or 0),
                        last_updated_at=r["last_seen_date"],
                    )
                    c.rank_score = score
                    out[uid] = c

            # ② 机制2: 随机抽取该平台新书榜中的作品（不看热度）
            new_rows = _latest_entries(
                "l.platform = ? AND (l.rank_family LIKE '%新书%' "
                "OR l.rank_sub_cat LIKE '%新书%')",
                [platform],
            )
            rng = random.Random(f"{platform}:{category}")
            pick = rng.sample(new_rows, min(5, len(new_rows))) if new_rows else []
            for r in pick:
                uid = str(r["novel_uid"])
                if uid in out:
                    continue
                c = WorkCandidate(
                    novel_id=uid, title="", platform=platform,
                    category=category,
                    collections=int(r["total_recommend"] or 0),
                    comments=0,
                    monthly_ticket=int(r["reading_count"] or 0),
                    word_count=int(r["total_words"] or 0),
                    last_updated_at=r["last_seen_date"],
                )
                c.rank_score = 0.35   # 新书随机样本：中低分入池
                out[uid] = c
    except sqlite3.OperationalError as e:
        logger.warning("board-candidate query failed (%s)", e)
        return []
    return list(out.values())


# 整平台代表作的四个维度配额（共 16 部，去重后近似）。
_PICK_TOP_RANK = 3     # 横跨所有榜单、总是处于榜单前列
_PICK_TOP_HEAT = 7     # 热度最高
_PICK_TOP_STABLE = 3   # 上榜次数最多（发挥最稳定）
_PICK_TOP_NEW = 3      # 各新书榜抽样


def _fetch_platform_candidates(
    crawler_db: str, platform: str, seed: int | None = None,
) -> list[WorkCandidate]:
    """整平台代表作（不限单一榜单）— 四个不同维度共同展现平台风格，跨桶去重：

    · 总排行最高（3）：横跨多个榜单、平均名次最靠前（持续居于榜单前列）
    · 热度最高（7）
    · 上榜最多·稳定（3）：历史上榜次数最多（覆盖所有快照，发挥稳定）
    · 新书榜抽样（3）：仅出现在新书榜的作品随机抽取

    指标取自全部快照（非仅最新），avg_recip = 平均 1/名次 衡量「总是靠前」，
    coverage = 上过的不同榜单数衡量「横跨榜单」。非新书榜优先（先填主榜维度）。"""
    try:
        with sqlite3.connect(crawler_db) as con:
            con.row_factory = sqlite3.Row
            rows = con.execute(
                """
                SELECT e.novel_uid AS uid,
                       COUNT(*) AS appearances,
                       COUNT(DISTINCT l.rank_list_id) AS coverage,
                       AVG(1.0 / e.rank) AS avg_recip,
                       MAX(COALESCE(e.total_recommend,0)
                           + COALESCE(e.reading_count,0)) AS max_heat,
                       MAX(n.total_words) AS words,
                       MAX(n.last_seen_date) AS last,
                       MAX(CASE WHEN l.rank_family LIKE '%新书%'
                                  OR l.rank_sub_cat LIKE '%新书%'
                                THEN 1 ELSE 0 END) AS in_new,
                       MAX(CASE WHEN l.rank_family LIKE '%新书%'
                                  OR l.rank_sub_cat LIKE '%新书%'
                                THEN 0 ELSE 1 END) AS in_main
                FROM rank_entries e
                JOIN rank_snapshots s ON s.snapshot_id = e.snapshot_id
                JOIN rank_lists l ON l.rank_list_id = s.rank_list_id
                JOIN novels n ON n.novel_uid = e.novel_uid
                WHERE l.platform = ?
                GROUP BY e.novel_uid
                """,
                [platform],
            ).fetchall()
    except sqlite3.OperationalError as e:
        logger.warning("platform-candidate query failed (%s)", e)
        return []
    if not rows:
        return []

    agg = {str(r["uid"]): dict(r) for r in rows}
    out: dict[str, WorkCandidate] = {}

    def _add(uid: str, reason: str, score: float) -> None:
        if uid in out:
            return
        a = agg[uid]
        c = WorkCandidate(
            novel_id=uid, title="", platform=platform, category="",
            collections=0, comments=0, monthly_ticket=int(a["max_heat"] or 0),
            word_count=int(a["words"] or 0), last_updated_at=a["last"],
        )
        c.rank_score = score
        c.selection_reason = reason
        out[uid] = c

    uids = list(agg.keys())
    main = [u for u in uids if agg[u]["in_main"]]
    new_only = [u for u in uids if agg[u]["in_new"] and not agg[u]["in_main"]]

    def _fill(ordered: list[str], reason: str, score: float, n: int) -> None:
        """填满该维度配额（跳过已被其它维度选中的，保证去重后凑齐 n 部）。"""
        added = 0
        for u in ordered:
            if u in out:
                continue
            _add(u, reason, score)
            added += 1
            if added >= n:
                break

    # ① 总排行最高：取主榜(非新书优先)中横跨多榜(coverage>=2)、平均名次最靠前的；
    #    不足则放宽 coverage / 到全部。新书榜专属作品留给维度④。
    cross = ([u for u in main if (agg[u]["coverage"] or 0) >= 2]
             or main or uids)
    _fill(sorted(cross, key=lambda u: (-(agg[u]["avg_recip"] or 0),
                                       -(agg[u]["coverage"] or 0))),
          "总排行最高", 0.95, _PICK_TOP_RANK)
    # ② 热度最高
    _fill(sorted(uids, key=lambda u: -(agg[u]["max_heat"] or 0)),
          "热度最高", 0.85, _PICK_TOP_HEAT)
    # ③ 上榜最多·稳定（历史上榜次数）
    _fill(sorted(uids, key=lambda u: -(agg[u]["appearances"] or 0)),
          "上榜最多·稳定", 0.75, _PICK_TOP_STABLE)
    # ④ 新书榜抽样（仅在新书榜的作品；不足则放宽到所有上过新书榜的）
    pool = new_only or [u for u in uids if agg[u]["in_new"]]
    if pool:
        rng = random.Random(seed if seed is not None else f"{platform}:new")
        rng.shuffle(pool)
        _fill(pool, "新书榜抽样", 0.55, _PICK_TOP_NEW)
    return list(out.values())


def _fetch_raw_candidates(
    crawler_db: str, platform: str, category: str,
) -> list[WorkCandidate]:
    """Pull candidates from the crawler DB. Prefers the real board-based
    selection (this repo's market schema); falls back to the legacy
    standalone-crawler ``novels.category`` shape."""
    if not Path(crawler_db).exists():
        logger.warning("crawler DB not found at %s", crawler_db)
        return []
    board = _fetch_board_candidates(crawler_db, platform, category)
    if board:
        return board
    try:
        with sqlite3.connect(crawler_db) as con:
            con.row_factory = sqlite3.Row
            rows = con.execute(
                "SELECT * FROM novels WHERE platform = ? AND category = ?",
                (platform, category),
            ).fetchall()
    except sqlite3.OperationalError as e:
        logger.warning("crawler DB schema mismatch (%s) — returning empty", e)
        return []
    candidates: list[WorkCandidate] = []
    for r in rows:
        d = dict(r)
        candidates.append(WorkCandidate(
            novel_id=str(d.get("novel_id") or d.get("id") or ""),
            title=str(d.get("title") or "").strip(),
            platform=platform,
            category=category,
            collections=int(d.get("collection_count") or d.get("collections") or 0),
            comments=int(d.get("comment_count") or d.get("comments") or 0),
            monthly_ticket=int(d.get("monthly_ticket") or 0),
            word_count=int(d.get("word_count") or d.get("words") or 0),
            last_updated_at=d.get("last_updated_at") or d.get("updated_at"),
        ))
    return candidates


def _filter_eligible(candidates: list[WorkCandidate]) -> list[WorkCandidate]:
    """Apply spec-mandated minimum thresholds.

    Adaptive: starts with spec's strict ``>= median*2 / median*1.5``
    filter; if that leaves zero candidates (small-corpus case) it
    relaxes to a single >= median pass so the pipeline still has
    something to work with."""
    if not candidates:
        return []
    cs = sorted(c.collections for c in candidates)
    cm = sorted(c.comments for c in candidates)
    median_c = cs[len(cs) // 2] if cs else 0
    median_m = cm[len(cm) // 2] if cm else 0

    strict = [
        c for c in candidates
        if c.collections >= median_c * 2
        and c.comments >= median_m * 1.5
        and c.word_count >= _MIN_WORD_COUNT
    ]
    if strict:
        return strict
    # Relaxed pass — keeps the pipeline runnable on small corpora.
    relaxed = [
        c for c in candidates
        if c.collections >= median_c
        and c.comments >= median_m
        and c.word_count >= _MIN_WORD_COUNT
    ]
    if relaxed:
        logger.info(
            "strict eligibility filter removed all candidates; "
            "falling back to relaxed median filter (%d kept)", len(relaxed),
        )
        return relaxed
    # Last resort: keep everything above the word-count floor.
    return [c for c in candidates if c.word_count >= _MIN_WORD_COUNT]


def _normalize(values: list[int]) -> list[float]:
    """Min-max normalize to [0, 1]. Returns 0s if all values equal."""
    if not values:
        return []
    mx, mn = max(values), min(values)
    span = mx - mn
    if span == 0:
        return [0.0 for _ in values]
    return [(v - mn) / span for v in values]


def _score(candidates: list[WorkCandidate]) -> None:
    """In-place: assign ``rank_score`` per the spec weights."""
    if not candidates:
        return
    n_coll = _normalize([c.collections for c in candidates])
    n_comm = _normalize([c.comments for c in candidates])
    n_tick = _normalize([c.monthly_ticket for c in candidates])
    n_word = _normalize([c.word_count for c in candidates])
    for i, c in enumerate(candidates):
        c.rank_score = (
            _WEIGHTS["collections"]    * n_coll[i]
            + _WEIGHTS["comments"]     * n_comm[i]
            + _WEIGHTS["monthly_ticket"] * n_tick[i]
            + _WEIGHTS["word_count"]   * n_word[i]
        )


def _persist(
    db_path: str, candidates: list[WorkCandidate],
    holdout_ids: set[str], selection_round: int,
) -> list[str]:
    """INSERT OR REPLACE selected candidates into
    ``representative_works_pool``. Returns the list of work_ids
    written (new + replaced)."""
    work_ids: list[str] = []
    with sqlite3.connect(db_path) as con:
        for c in candidates:
            wid = f"rw_{uuid.uuid4().hex[:12]}"
            con.execute(
                "INSERT OR REPLACE INTO representative_works_pool "
                "(work_id, platform, category, source_db_novel_id, "
                " rank_score, collection_count, comment_count, "
                " monthly_ticket, word_count, last_updated_at, "
                " selected_for_extraction, selection_round, is_holdout, "
                " selection_reason) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)",
                (wid, c.platform, c.category, c.novel_id,
                 c.rank_score, c.collections, c.comments,
                 c.monthly_ticket, c.word_count, c.last_updated_at,
                 selection_round, 1 if c.novel_id in holdout_ids else 0,
                 c.selection_reason),
            )
            work_ids.append(wid)
        con.commit()
    return work_ids


def select(
    db_path: str, platform: str, category: str,
    *,
    top_k: int = _DEFAULT_TOP_K,
    selection_round: int = 1,
    crawler_db: str | None = None,
    seed: int | None = None,
) -> dict[str, Any]:
    """Run the full Phase-1 selection pipeline.

    Returns a summary dict:
        {selected: N, holdout: M, work_ids: [...], crawler_db: path}
    """
    cdb = crawler_db or _resolve_crawler_db()
    holdout_ids: set[str] = set()
    if not category:
        # 整平台多维度代表作（用户只选平台，不再单选榜单）— 已按维度精选并
        # 预打分，跳过 median/词数过滤；不留 holdout，让多样集全部可选。
        top = _fetch_platform_candidates(cdb, platform, seed=seed)
        top.sort(key=lambda c: c.rank_score, reverse=True)
    else:
        raw = _fetch_raw_candidates(cdb, platform, category)
        # Board-based candidates (real schema) arrive pre-scored by
        # rank + heat and deliberately include random 新书 samples
        # (机制2) — the legacy median/word-count filter and
        # collections-weight rescoring would strip them, so skip both.
        if raw and all(c.rank_score > 0 for c in raw):
            eligible = list(raw)
        else:
            eligible = _filter_eligible(raw)
            _score(eligible)
        eligible.sort(key=lambda c: c.rank_score, reverse=True)
        top = eligible[:top_k]

        # Holdout: 10% randomly sampled from the top set.
        holdout_n = max(1, int(len(top) * _HOLDOUT_FRACTION)) if top else 0
        rng = random.Random(seed)
        if top:
            for c in rng.sample(top, min(holdout_n, len(top))):
                holdout_ids.add(c.novel_id)

    work_ids = _persist(db_path, top, holdout_ids, selection_round)
    return {
        "selected":   len(top),
        "holdout":    len(holdout_ids),
        "work_ids":   work_ids,
        "crawler_db": crawler_db or _resolve_crawler_db(),
    }


def exclude_work(db_path: str, work_id: str) -> bool:
    """Mark a work as excluded so the extractor skips it."""
    with sqlite3.connect(db_path) as con:
        cur = con.execute(
            "UPDATE representative_works_pool "
            "SET selected_for_extraction = 0 WHERE work_id = ?",
            (work_id,),
        )
        con.commit()
    return cur.rowcount > 0


def list_selected(
    db_path: str, platform: str, category: str,
    *, include_holdout: bool = False,
) -> list[dict]:
    where = ["platform = ?", "category = ?", "selected_for_extraction = 1"]
    params: list[Any] = [platform, category]
    if not include_holdout:
        where.append("is_holdout = 0")
    with sqlite3.connect(db_path) as con:
        con.row_factory = sqlite3.Row
        rows = con.execute(
            f"SELECT * FROM representative_works_pool "
            f"WHERE {' AND '.join(where)} ORDER BY rank_score DESC",
            params,
        ).fetchall()
    return [dict(r) for r in rows]
