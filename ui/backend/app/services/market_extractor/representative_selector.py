"""Phase 1: pick representative works per (platform, category).

Reads from the crawler DB (path configurable via ``WN_CRAWLER_DB``;
defaults to ``data/InkOctoBot_Crawler.db``), scores each novel,
selects top 30, randomly tags 3 as holdout, persists into
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


def _resolve_crawler_db() -> str:
    """Crawler DB path. Honors ``WN_CRAWLER_DB`` env override; otherwise
    looks in ``data/InkOctoBot_Crawler.db`` relative to the repo root."""
    env = os.environ.get("WN_CRAWLER_DB")
    if env:
        return env
    repo_root = Path(__file__).resolve().parents[5]
    return str(repo_root / "data" / "InkOctoBot_Crawler.db")


def _fetch_raw_candidates(
    crawler_db: str, platform: str, category: str,
) -> list[WorkCandidate]:
    """Pull candidates from the crawler DB. Returns ``[]`` if the DB
    is missing or the schema doesn't match the expected shape."""
    if not Path(crawler_db).exists():
        logger.warning("crawler DB not found at %s", crawler_db)
        return []
    try:
        with sqlite3.connect(crawler_db) as con:
            con.row_factory = sqlite3.Row
            # Tolerant column lookup — crawler schema isn't pinned here;
            # we accept any of several plausible names.
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
                " selected_for_extraction, selection_round, is_holdout) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)",
                (wid, c.platform, c.category, c.novel_id,
                 c.rank_score, c.collections, c.comments,
                 c.monthly_ticket, c.word_count, c.last_updated_at,
                 selection_round, 1 if c.novel_id in holdout_ids else 0),
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
    raw = _fetch_raw_candidates(
        crawler_db or _resolve_crawler_db(), platform, category,
    )
    eligible = _filter_eligible(raw)
    _score(eligible)
    eligible.sort(key=lambda c: c.rank_score, reverse=True)
    top = eligible[:top_k]

    # Holdout: 10% randomly sampled from the top set.
    holdout_n = max(1, int(len(top) * _HOLDOUT_FRACTION)) if top else 0
    rng = random.Random(seed)
    holdout_ids: set[str] = set()
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
