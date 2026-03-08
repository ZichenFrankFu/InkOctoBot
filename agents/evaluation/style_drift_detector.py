"""
Style Drift Detector — checks if generated text drifts from target style.

Compares statistical features (sentence length, dialogue ratio, etc.)
against the project's style profile.
"""
from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger("inkoctobot.agents.evaluation.style_drift_detector")


class StyleDriftDetector:
    """Detects style drift using statistical comparison."""

    def __init__(self, target_profile: dict[str, float] | None = None):
        self.target = target_profile or {}

    def set_target(self, profile: dict[str, float]) -> None:
        self.target = profile

    def detect(self, text: str) -> dict[str, Any]:
        """Compare text style features against the target profile."""
        current = self._extract_features(text)
        if not self.target:
            return {"drifted": False, "features": current, "note": "No target profile set"}

        drifts: list[dict[str, Any]] = []
        for key, target_val in self.target.items():
            current_val = current.get(key, 0)
            if target_val > 0:
                deviation = abs(current_val - target_val) / target_val
                if deviation > 0.3:  # >30% deviation
                    drifts.append({
                        "feature": key,
                        "target": round(target_val, 3),
                        "current": round(current_val, 3),
                        "deviation": round(deviation, 3),
                    })

        return {
            "drifted": len(drifts) > 0,
            "drifts": drifts,
            "features": current,
            "score": max(0, 100 - len(drifts) * 20),
        }

    @staticmethod
    def _extract_features(text: str) -> dict[str, float]:
        sentences = re.split(r'[。！？]+', text)
        sentences = [s.strip() for s in sentences if s.strip()]
        avg_sent_len = sum(len(s) for s in sentences) / max(len(sentences), 1)

        dialogues = re.findall(r'[""「」]', text)
        dialogue_ratio = len(dialogues) / max(len(text), 1) * 50

        paragraphs = [p for p in text.split("\n") if p.strip()]
        avg_para_len = sum(len(p) for p in paragraphs) / max(len(paragraphs), 1)

        return {
            "avg_sentence_length": avg_sent_len,
            "dialogue_ratio": dialogue_ratio,
            "avg_paragraph_length": avg_para_len,
            "total_length": len(text),
        }
