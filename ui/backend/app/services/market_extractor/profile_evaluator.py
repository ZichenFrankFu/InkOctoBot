"""Phase 5 — holdout-based profile evaluation.

For each holdout work: compare the loader_payload-implied style
features against the holdout's real chapter_features. Simple
cosine-style similarity over a small numerical feature vector —
not a generation-quality metric, just a sanity check.

Future work could plug a Writer round-trip + style-similarity score,
but that needs the writer agent + the full Phase-5 spec (`writer
generates a chapter from profile alone` flow). For now this is a
deterministic, zero-LLM evaluator.
"""
from __future__ import annotations

import json
import logging
import math
import sqlite3
from typing import Any

logger = logging.getLogger("inkoctobot.market_extractor.profile_evaluator")


_NUMERICAL_FEATURES = (
    "avg_sentence_length", "dialogue_ratio",
    "short_sentence_ratio", "long_sentence_ratio",
    "adjective_density", "verb_density",
    "exclamation_density", "question_density",
    "idiom_density", "classical_word_density",
    "single_sentence_paragraph_ratio",
)


def _mean_feature_vector(rows: list[dict]) -> dict[str, float]:
    """Average each numerical feature across rows. Returns 0s on empty."""
    if not rows:
        return {f: 0.0 for f in _NUMERICAL_FEATURES}
    out: dict[str, float] = {}
    for f in _NUMERICAL_FEATURES:
        vals = [float(r.get(f) or 0) for r in rows]
        out[f] = sum(vals) / len(vals)
    return out


def _cosine(a: dict[str, float], b: dict[str, float]) -> float:
    keys = list(set(a) & set(b))
    if not keys:
        return 0.0
    dot = sum(a[k] * b[k] for k in keys)
    na = math.sqrt(sum(a[k] ** 2 for k in keys))
    nb = math.sqrt(sum(b[k] ** 2 for k in keys))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def _label_for(score: float) -> str:
    if score >= 0.85:
        return "high"
    if score >= 0.65:
        return "medium"
    return "low"


def evaluate(db_path: str, profile_id: str) -> dict[str, Any]:
    """Score a profile against its holdout set; update the profile row."""
    with sqlite3.connect(db_path) as con:
        con.row_factory = sqlite3.Row
        profile = con.execute(
            "SELECT platform, category, source_works_ids_json "
            "FROM platform_profiles WHERE profile_id = ?",
            (profile_id,),
        ).fetchone()
        if profile is None:
            raise ValueError(f"profile {profile_id!r} not found")

        # Source works' chapter feature vectors → "expected profile shape".
        source_ids = json.loads(profile["source_works_ids_json"] or "[]")
        if not source_ids:
            return {"profile_id": profile_id, "score": 0.0, "confidence": "low"}
        ph = ",".join("?" * len(source_ids))
        source_rows = [dict(r) for r in con.execute(
            f"SELECT * FROM chapter_features WHERE work_id IN ({ph})",
            source_ids,
        ).fetchall()]
        source_vec = _mean_feature_vector(source_rows)

        # Holdout works' chapter feature vectors.
        holdout_ids_rows = con.execute(
            "SELECT work_id FROM representative_works_pool "
            "WHERE platform = ? AND category = ? AND is_holdout = 1",
            (profile["platform"], profile["category"]),
        ).fetchall()
        holdout_ids = [r[0] for r in holdout_ids_rows]
        if not holdout_ids:
            return {"profile_id": profile_id, "score": 0.0, "confidence": "low",
                     "reason": "no_holdout_set"}
        ph_h = ",".join("?" * len(holdout_ids))
        holdout_rows = [dict(r) for r in con.execute(
            f"SELECT * FROM chapter_features WHERE work_id IN ({ph_h})",
            holdout_ids,
        ).fetchall()]
        holdout_vec = _mean_feature_vector(holdout_rows)

        score = _cosine(source_vec, holdout_vec)
        label = _label_for(score)

        con.execute(
            "UPDATE platform_profiles "
            "SET holdout_similarity_score = ?, confidence_label = ? "
            "WHERE profile_id = ?",
            (round(score, 4), label, profile_id),
        )
        con.commit()

    return {
        "profile_id":            profile_id,
        "holdout_works_count":   len(holdout_ids),
        "source_works_count":    len(source_ids),
        "holdout_similarity":    round(score, 4),
        "confidence":            label,
    }
