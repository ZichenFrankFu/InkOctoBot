"""
LoRA quality filter.

Filters training samples for LoRA fine-tuning by checking length,
slop density, repetition, and style consistency with a target profile.
Reuses ``SlopDetector`` and ``StyleExtractor`` from the evaluation /
preprocessing layers.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("inkoctobot.preprocessing.lora.quality_filter")


@dataclass
class FilterResult:
    """Outcome of a filtering pass."""
    passed: list[dict[str, Any]] = field(default_factory=list)
    rejected: list[dict[str, Any]] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)


def filter_samples(
    samples: list[dict[str, Any]],
    *,
    target_style: dict[str, Any] | None = None,
    min_length: int = 50,
    max_length: int = 8000,
    max_slop_score: float = 30.0,
    max_style_deviations: int = 4,
) -> FilterResult:
    """Filter a list of training samples.

    Each sample dict must have at least a ``"text"`` key.

    Parameters
    ----------
    target_style : dict, optional
        Style fingerprint to compare against (from ``StyleExtractor.extract``).
    min_length / max_length : int
        Character-length bounds.
    max_slop_score : float
        Maximum *density* of slop patterns (lower = stricter).
    max_style_deviations : int
        Maximum number of style dimensions that deviate > 30 % from *target_style*.
    """
    from agents.evaluation.slop_detector import SlopDetector
    from preprocessing.style_extractor import StyleExtractor

    slop = SlopDetector()
    styler = StyleExtractor()

    passed: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    reason_counts: dict[str, int] = {}

    def _reject(sample: dict, reason: str) -> None:
        sample["_reject_reason"] = reason
        rejected.append(sample)
        reason_counts[reason] = reason_counts.get(reason, 0) + 1

    for s in samples:
        text = s.get("text", "")

        # 1. Length check
        if len(text) < min_length:
            _reject(s, "too_short")
            continue
        if len(text) > max_length:
            _reject(s, "too_long")
            continue

        # 2. Slop check
        slop_result = slop.detect(text)
        if slop_result["density"] > max_slop_score:
            _reject(s, "high_slop")
            continue

        # 3. Style consistency check
        if target_style:
            sample_style = styler.extract(text)
            cmp = styler.compare(target_style, sample_style)
            if cmp["deviation_count"] > max_style_deviations:
                _reject(s, "style_mismatch")
                continue

        passed.append(s)

    total = len(samples)
    result = FilterResult(
        passed=passed,
        rejected=rejected,
        stats={
            "total": total,
            "passed": len(passed),
            "rejected": len(rejected),
            "pass_rate": round(len(passed) / max(total, 1), 3),
            "reject_reasons": reason_counts,
        },
    )
    logger.info(
        "Filtered %d samples: %d passed, %d rejected (pass_rate=%.1f%%)",
        total, len(passed), len(rejected), result.stats["pass_rate"] * 100,
    )
    return result
