"""
Feature aggregator.

Aggregates multi-dimensional feature-extraction results (style fingerprint,
narrative, rhythm, rhetoric, shuangdian) into a single weighted score with
per-dimension breakdown and recommendations.
"""
from __future__ import annotations

from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from .presets import GenrePreset

# Default weights when no preset is provided
_DEFAULT_WEIGHTS: dict[str, float] = {
    "avg_sentence_length": 0.10,
    "dialogue_ratio": 0.15,
    "description_density": 0.10,
    "rhetoric_frequency": 0.10,
    "vocab_complexity": 0.15,
    "pacing": 0.20,
    "shuangdian": 0.20,
}

# Reference ranges for normalisation (mid-point and half-width)
_NORM: dict[str, tuple[float, float]] = {
    "avg_sentence_length": (20.0, 15.0),
    "dialogue_ratio":      (0.30, 0.30),
    "description_density": (0.10, 0.10),
    "rhetoric_frequency":  (3.0, 5.0),
    "vocab_complexity":    (0.50, 0.50),
}


def _normalise(key: str, value: float) -> float:
    """Map a raw feature value to 0–100 via reference ranges."""
    if key in _NORM:
        mid, half = _NORM[key]
        return max(0.0, min(100.0, 50.0 + (value - mid) / max(half, 1e-6) * 50.0))
    # Already 0–1 or 0–100; clamp to 100
    if 0.0 <= value <= 1.0:
        return value * 100.0
    return max(0.0, min(100.0, value))


def aggregate_features(
    style: dict[str, Any],
    narrative: dict[str, Any],
    rhythm: dict[str, Any],
    rhetoric: dict[str, Any] | None = None,
    shuangdian: dict[str, Any] | None = None,
    preset: GenrePreset | None = None,
) -> dict[str, Any]:
    """Aggregate multi-dimensional features into a scored summary.

    Returns::

        {"aggregated_score": float,
         "dimension_scores": {dim: float, ...},
         "recommendations": [str, ...]}
    """
    weights = (preset.feature_weights if preset else None) or _DEFAULT_WEIGHTS

    raw: dict[str, float] = {}

    # --- style dimensions ---
    for k in ("avg_sentence_length", "dialogue_ratio",
              "description_density", "vocab_complexity"):
        if k in style:
            raw[k] = float(style[k])

    if "rhetoric_frequency" in style:
        raw["rhetoric_frequency"] = float(style["rhetoric_frequency"])
    elif rhetoric and "density_per_1k" in rhetoric:
        raw["rhetoric_frequency"] = float(rhetoric["density_per_1k"])

    # --- pacing score ---
    pacing_profile = style.get("pacing_profile", {})
    if pacing_profile and preset and preset.pacing_profile:
        # Score = 100 − deviation between actual and target profile
        dev = sum(
            abs(pacing_profile.get(k, 0.0) - preset.pacing_profile.get(k, 0.0))
            for k in ("fast", "medium", "slow")
        )
        raw["pacing"] = max(0.0, 1.0 - dev)
    elif rhythm and rhythm.get("pacing_segments"):
        raw["pacing"] = 0.5  # neutral default

    # --- shuangdian score ---
    if shuangdian:
        per_ch = shuangdian.get("per_chapter", 0.0)
        coverage = shuangdian.get("chapter_coverage", 0.0)
        raw["shuangdian"] = min(1.0, (per_ch / 2.0 + coverage) / 2.0)

    # --- normalise & weight ---
    dim_scores: dict[str, float] = {}
    for dim, w in weights.items():
        if dim in raw:
            dim_scores[dim] = round(_normalise(dim, raw[dim]), 1)

    weighted_sum = sum(
        dim_scores[d] * weights.get(d, 0.0) for d in dim_scores
    )
    total_weight = sum(weights.get(d, 0.0) for d in dim_scores)
    aggregated = round(weighted_sum / max(total_weight, 1e-6), 1)

    # --- recommendations ---
    recs: list[str] = []
    if dim_scores.get("dialogue_ratio", 50) < 30:
        recs.append("对话比例偏低，考虑增加角色对话以提高可读性")
    if dim_scores.get("dialogue_ratio", 50) > 80:
        recs.append("对话比例偏高，建议增加叙述和环境描写")
    if dim_scores.get("shuangdian", 50) < 25:
        recs.append("爽点密度偏低，建议增加读者满足感高峰场景")
    if dim_scores.get("pacing", 50) < 30:
        recs.append("节奏与目标差异较大，检查快慢节奏分布")
    if dim_scores.get("rhetoric_frequency", 50) > 85:
        recs.append("修辞密度偏高，注意避免过度修饰影响流畅度")

    return {
        "aggregated_score": aggregated,
        "dimension_scores": dim_scores,
        "recommendations": recs,
    }
