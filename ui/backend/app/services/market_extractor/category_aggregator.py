"""Phase 4 part 1 — cross-work aggregation.

Compute distributions / quartiles / vocabulary across all (non-holdout)
works in a category. Side-effect: append the top-200 high-frequency
work-specific terms back into ``resources/dictionaries/genre/<cat>.txt``
so the next extraction pass filters them out (self-maintaining loop).
"""
from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from collections import Counter
from typing import Any

from .dictionaries import append_to_genre_dict

logger = logging.getLogger("inkoctobot.market_extractor.category_aggregator")


def _stats_dict(values: list[float]) -> dict[str, float]:
    if not values:
        return {"mean": 0, "std": 0, "p25": 0, "p50": 0, "p75": 0}
    s = sorted(values)
    n = len(s)
    mean = sum(s) / n
    var = sum((v - mean) ** 2 for v in s) / n
    std = var ** 0.5
    def _q(p):
        k = max(0, min(n - 1, int(round(p * (n - 1)))))
        return s[k]
    return {"mean": mean, "std": std,
            "p25": _q(0.25), "p50": _q(0.50), "p75": _q(0.75)}


def _distribution(values: list[Any]) -> dict[str, int]:
    """Frequency dict over non-null categorical values."""
    counts: Counter = Counter()
    for v in values:
        if v in (None, ""):
            continue
        counts[str(v)] += 1
    return dict(counts.most_common())


def _median_int(values: list[int]) -> int:
    filtered = [v for v in values if v]
    if not filtered:
        return 0
    s = sorted(filtered)
    n = len(s)
    return s[n // 2]


def _aggregate_top_words(
    db_path: str, work_ids: list[str], top_n: int = 200,
) -> list[dict]:
    """Cross-work: sum each word's score across all works' top-30 lists."""
    score: Counter[str] = Counter()
    with sqlite3.connect(db_path) as con:
        for wid in work_ids:
            row = con.execute(
                "SELECT work_top_words_json FROM work_aggregated_features "
                "WHERE work_id = ?",
                (wid,),
            ).fetchone()
            if not row or not row[0]:
                continue
            try:
                items = json.loads(row[0])
            except Exception:
                continue
            for it in items:
                if isinstance(it, dict):
                    score[str(it.get("term") or "")] += float(it.get("score") or 0)
                elif isinstance(it, list) and len(it) == 2:
                    score[str(it[0])] += float(it[1])
    return [
        {"term": t, "score": round(s, 2)}
        for t, s in score.most_common(top_n) if t
    ]


def aggregate(
    db_path: str, platform: str, category: str,
    *, update_genre_dict: bool = True,
) -> dict[str, Any]:
    """Run the full cross-work aggregation + persist + optionally update
    the genre vocabulary on disk."""
    # Pull eligible (non-holdout) works.
    with sqlite3.connect(db_path) as con:
        con.row_factory = sqlite3.Row
        works = [dict(r) for r in con.execute(
            "SELECT work_id FROM representative_works_pool "
            "WHERE platform = ? AND category = ? "
            "AND selected_for_extraction = 1 AND is_holdout = 0",
            (platform, category),
        ).fetchall()]
    if not works:
        return {"platform": platform, "category": category, "source_works_count": 0}

    work_ids = [w["work_id"] for w in works]
    in_clause = "(" + ",".join("?" * len(work_ids)) + ")"

    with sqlite3.connect(db_path) as con:
        con.row_factory = sqlite3.Row
        chapter_rows = [dict(r) for r in con.execute(
            f"SELECT * FROM chapter_features WHERE work_id IN {in_clause}",
            work_ids,
        ).fetchall()]
        agg_rows = [dict(r) for r in con.execute(
            f"SELECT * FROM work_aggregated_features WHERE work_id IN {in_clause}",
            work_ids,
        ).fetchall()]

    # Distributions over LLM categorical fields.
    opening_dist = _distribution([r["opening_hook_type"] for r in chapter_rows])
    cheat_dist   = _distribution([r["protagonist_cheat_type"] for r in chapter_rows])
    agency_dist  = _distribution([r["protagonist_agency"] for r in chapter_rows])
    world_dist   = _distribution([r["worldview_power_system"] for r in chapter_rows])
    style_dist   = _distribution([
        r["dominant_writing_style"] for r in agg_rows
    ])
    emo_dist     = _distribution([r["emotional_tone"] for r in chapter_rows])
    info_dist    = _distribution([r["info_disclosure_strategy"] for r in chapter_rows])
    hook_dist    = _distribution([r["chapter_end_hook_type"] for r in chapter_rows])

    # Numerical stats.
    wc_stats = _stats_dict([r["word_count"] or 0 for r in chapter_rows])
    dr_stats = _stats_dict([r["dialogue_ratio"] or 0 for r in chapter_rows])
    setting_stats = _stats_dict([
        r["setting_word_ratio"] or 0 for r in chapter_rows
    ])

    # Node-position medians.
    first_break = _median_int([
        json.loads(r["node_positions_json"] or "{}").get("first_breakthrough_chapter") or 0
        for r in agg_rows
    ])
    antagonist_med = _median_int([
        json.loads(r["node_positions_json"] or "{}").get("antagonist_first_chapter") or 0
        for r in agg_rows
    ])
    face_slap_med = _median_int([
        json.loads(r["node_positions_json"] or "{}").get("first_face_slap_chapter") or 0
        for r in agg_rows
    ])

    # Genre vocabulary (top 200 across works).
    top_words = _aggregate_top_words(db_path, work_ids, top_n=200)
    if update_genre_dict:
        appended = append_to_genre_dict(category, [w["term"] for w in top_words])
        logger.info("appended %d new terms to %s genre dict", appended, category)

    # Neologism metadata only (no cross-work content aggregation per spec).
    avg_neo = (
        sum(r["neologism_count"] or 0 for r in agg_rows) / len(agg_rows)
    )
    neo_type_dist: Counter = Counter()
    for r in agg_rows:
        try:
            by_type = json.loads(r["neologism_count_by_type_json"] or "{}")
            for k, v in by_type.items():
                neo_type_dist[k] += int(v)
        except Exception:
            pass

    payload = {
        "stats_id":                                  f"cas_{uuid.uuid4().hex[:12]}",
        "platform":                                  platform,
        "category":                                  category,
        "source_works_count":                        len(work_ids),
        "source_works_ids_json":                     json.dumps(work_ids, ensure_ascii=False),
        "opening_hook_type_distribution_json":       json.dumps(opening_dist, ensure_ascii=False),
        "protagonist_cheat_type_distribution_json":  json.dumps(cheat_dist, ensure_ascii=False),
        "protagonist_agency_distribution_json":      json.dumps(agency_dist, ensure_ascii=False),
        "worldview_type_distribution_json":          json.dumps(world_dist, ensure_ascii=False),
        "writing_style_distribution_json":           json.dumps(style_dist, ensure_ascii=False),
        "emotional_tone_distribution_json":          json.dumps(emo_dist, ensure_ascii=False),
        "info_disclosure_distribution_json":         json.dumps(info_dist, ensure_ascii=False),
        "chapter_word_count_stats_json":             json.dumps(wc_stats, ensure_ascii=False),
        "dialogue_ratio_stats_json":                 json.dumps(dr_stats, ensure_ascii=False),
        "setting_word_ratio_stats_json":             json.dumps(setting_stats, ensure_ascii=False),
        "first_breakthrough_chapter_median":         first_break,
        "antagonist_first_chapter_median":           antagonist_med,
        "first_face_slap_chapter_median":            face_slap_med,
        "genre_vocabulary_top_json":                 json.dumps(top_words, ensure_ascii=False),
        "avg_neologisms_per_work":                   avg_neo,
        "neologism_type_distribution_json":          json.dumps(dict(neo_type_dist), ensure_ascii=False),
        "chapter_end_hook_type_distribution_json":   json.dumps(hook_dist, ensure_ascii=False),
    }

    with sqlite3.connect(db_path) as con:
        cols = ",".join(payload.keys())
        ph = ",".join("?" * len(payload))
        con.execute(
            f"INSERT INTO category_aggregated_stats ({cols}) VALUES ({ph})",
            list(payload.values()),
        )
        con.commit()
    return payload
