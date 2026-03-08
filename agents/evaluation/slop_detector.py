"""
AI Flavor (Slop) Detector — identifies AI-generated text patterns.

Based on Slop Detection research (arXiv:2509.19163).
Uses pattern matching + configurable pattern library.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger("inkoctobot.agents.evaluation.slop_detector")

_PATTERNS_PATH = Path(__file__).resolve().parent.parent.parent / "config" / "slop_patterns.json"

# Built-in Chinese slop patterns
_DEFAULT_PATTERNS: list[dict[str, Any]] = [
    {"pattern": r"不禁", "weight": 0.3, "category": "cliche_phrase"},
    {"pattern": r"嘴角.*上扬", "weight": 0.5, "category": "cliche_action"},
    {"pattern": r"眼中闪过.*光芒", "weight": 0.5, "category": "cliche_action"},
    {"pattern": r"深吸一口气", "weight": 0.3, "category": "cliche_action"},
    {"pattern": r"心中一凛", "weight": 0.3, "category": "cliche_phrase"},
    {"pattern": r"宛如.*般", "weight": 0.2, "category": "cliche_simile"},
    {"pattern": r"如同.*一般", "weight": 0.2, "category": "cliche_simile"},
    {"pattern": r"紧紧握住.*拳头", "weight": 0.4, "category": "cliche_action"},
    {"pattern": r"值得注意的是", "weight": 0.6, "category": "ai_tell"},
    {"pattern": r"总而言之", "weight": 0.5, "category": "ai_tell"},
    {"pattern": r"综上所述", "weight": 0.7, "category": "ai_tell"},
    {"pattern": r"让我们", "weight": 0.4, "category": "ai_tell"},
]


class SlopDetector:
    """Rule-based AI flavor detection."""

    def __init__(self, patterns_path: str | Path | None = None):
        self.patterns = self._load_patterns(patterns_path)

    def _load_patterns(self, path: str | Path | None) -> list[dict[str, Any]]:
        p = Path(path) if path else _PATTERNS_PATH
        if p.exists():
            try:
                with open(p, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                    if isinstance(loaded, list):
                        return loaded
                    return loaded.get("patterns", _DEFAULT_PATTERNS)
            except (json.JSONDecodeError, KeyError):
                pass
        return _DEFAULT_PATTERNS

    def detect(self, text: str) -> dict[str, Any]:
        """Detect AI slop patterns in text."""
        matches: list[dict[str, Any]] = []
        total_weight = 0.0

        for pat in self.patterns:
            regex = pat.get("pattern", "")
            found = re.findall(regex, text)
            if found:
                matches.append({
                    "pattern": regex,
                    "category": pat.get("category", "unknown"),
                    "count": len(found),
                    "weight": pat.get("weight", 0.5),
                    "examples": found[:3],
                })
                total_weight += pat.get("weight", 0.5) * len(found)

        # Normalize score: 100 = no slop, 0 = heavy slop
        text_len = max(len(text), 1)
        density = total_weight / (text_len / 1000)
        score = max(0, 100 - density * 20)

        return {
            "score": round(score, 1),
            "matches": matches,
            "density": round(density, 3),
            "has_issues": score < 70,
        }
