"""Phase 3 — fold per-chapter features into one work_aggregated_features row.

Numerical fields → mean / median.
Categorical fields → mode (most frequent value).
List fields → union across chapters.
Opening fields → take chapter-1 values.
Node positions → first chapter where the marker appears.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from collections import Counter
from typing import Any

logger = logging.getLogger("inkoctobot.market_extractor.work_aggregator")


def _load_chapter_rows(db_path: str, work_id: str) -> list[dict]:
    with sqlite3.connect(db_path) as con:
        con.row_factory = sqlite3.Row
        rows = con.execute(
            "SELECT * FROM chapter_features WHERE work_id = ? "
            "ORDER BY chapter_num",
            (work_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def _median(values: list[float]) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    n = len(s)
    if n % 2:
        return float(s[n // 2])
    return (s[n // 2 - 1] + s[n // 2]) / 2


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _mode(values: list[Any]) -> Any:
    filtered = [v for v in values if v]
    if not filtered:
        return None
    return Counter(filtered).most_common(1)[0][0]


def _union_json(values: list[str]) -> list:
    """Union of JSON-encoded lists. Tolerates non-list payloads."""
    out: list = []
    seen: set = set()
    for raw in values:
        if not raw:
            continue
        try:
            parsed = json.loads(raw)
        except Exception:
            continue
        if not isinstance(parsed, list):
            continue
        for item in parsed:
            key = json.dumps(item, ensure_ascii=False, sort_keys=True)
            if key in seen:
                continue
            seen.add(key)
            out.append(item)
    return out


def _node_positions(rows: list[dict]) -> dict[str, int | None]:
    """First chapter each notable marker appears."""
    out: dict[str, int | None] = {
        "first_breakthrough_chapter": None,
        "antagonist_first_chapter":   None,
        "first_face_slap_chapter":    None,
        "first_decision_chapter":     None,
    }
    for r in rows:
        if (out["antagonist_first_chapter"] is None
                and r.get("antagonist_first_chapter")):
            out["antagonist_first_chapter"] = r["antagonist_first_chapter"]
        if (out["first_face_slap_chapter"] is None
                and r.get("first_face_slap_chapter")):
            out["first_face_slap_chapter"] = r["first_face_slap_chapter"]
        if (out["first_decision_chapter"] is None
                and r.get("protagonist_first_decision_chapter")):
            out["first_decision_chapter"] = r["protagonist_first_decision_chapter"]
    return out


def _aggregate_tfidf(rows: list[dict], top_n: int = 30) -> list:
    """Sum-aggregate per-chapter TF-IDF scores → work-level top words."""
    score: Counter[str] = Counter()
    for r in rows:
        raw = r.get("tfidf_top_words_json")
        if not raw:
            continue
        try:
            items = json.loads(raw)
        except Exception:
            continue
        for it in items:
            if isinstance(it, list) and len(it) == 2:
                term, sc = it[0], it[1]
            elif isinstance(it, dict) and "term" in it:
                term, sc = it["term"], it.get("score", 1)
            else:
                continue
            try:
                score[str(term)] += float(sc)
            except Exception:
                pass
    return [
        {"term": t, "score": round(s, 3)}
        for t, s in score.most_common(top_n)
    ]


def _count_neologisms(db_path: str, work_id: str) -> tuple[int, dict[str, int]]:
    with sqlite3.connect(db_path) as con:
        total = con.execute(
            "SELECT COUNT(*) FROM work_neologisms WHERE work_id = ?",
            (work_id,),
        ).fetchone()[0]
        by_type = dict(con.execute(
            "SELECT neologism_type, COUNT(*) FROM work_neologisms "
            "WHERE work_id = ? GROUP BY neologism_type",
            (work_id,),
        ).fetchall())
    return total, by_type


def aggregate(db_path: str, work_id: str) -> dict[str, Any]:
    """Compute + persist work-level aggregation. Returns the persisted row."""
    rows = _load_chapter_rows(db_path, work_id)
    if not rows:
        return {"work_id": work_id, "chapters_found": 0}

    # Look up platform / category from the pool table.
    with sqlite3.connect(db_path) as con:
        pool_row = con.execute(
            "SELECT platform, category FROM representative_works_pool "
            "WHERE work_id = ?",
            (work_id,),
        ).fetchone()
    platform, category = (pool_row or ("", ""))

    avg_wc = _mean([r["word_count"] or 0 for r in rows])
    median_wc = _median([r["word_count"] or 0 for r in rows])
    avg_dr = _mean([r["dialogue_ratio"] or 0 for r in rows])
    avg_pc = _mean([r["paragraph_count"] or 0 for r in rows])
    dominant_tone = _mode([r.get("emotional_tone") for r in rows])
    dominant_style = _mode([r.get("writing_style_keywords_json") for r in rows])

    first = rows[0]
    opening_pattern = first.get("first_sentence_type") or ""
    opening_hook = first.get("opening_hook_type") or ""
    opening_desc = first.get("opening_hook_description") or ""

    devices = _union_json([
        r.get("writing_style_keywords_json") or "" for r in rows
    ])
    nodes = _node_positions(rows)
    top_words = _aggregate_tfidf(rows)
    neo_total, neo_by_type = _count_neologisms(db_path, work_id)

    payload = {
        "work_id":                       work_id,
        "platform":                      platform,
        "category":                      category,
        "avg_chapter_word_count":        avg_wc,
        "median_chapter_word_count":     median_wc,
        "avg_dialogue_ratio":            avg_dr,
        "avg_paragraph_count":           avg_pc,
        "dominant_emotional_tone":       dominant_tone,
        "dominant_writing_style":        dominant_style,
        "opening_pattern":               opening_pattern,
        "opening_hook_type":             opening_hook,
        "opening_hook_description":      opening_desc,
        "signature_devices_used_json":   json.dumps(devices, ensure_ascii=False),
        "node_positions_json":           json.dumps(nodes, ensure_ascii=False),
        "work_top_words_json":           json.dumps(top_words, ensure_ascii=False),
        "neologism_count":               neo_total,
        "neologism_count_by_type_json":  json.dumps(neo_by_type, ensure_ascii=False),
    }

    with sqlite3.connect(db_path) as con:
        cols = ",".join(payload.keys())
        ph = ",".join("?" * len(payload))
        con.execute(
            f"INSERT OR REPLACE INTO work_aggregated_features ({cols}) "
            f"VALUES ({ph})",
            list(payload.values()),
        )
        con.commit()
    return payload
