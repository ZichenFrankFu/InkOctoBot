"""
Rhythm Analyzer — analyzes narrative rhythm and tension curves.

Measures pacing through sentence length variation, paragraph density,
dialogue frequency, and action density across chapter segments.
"""
from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger("inkoctobot.preprocessing.rhythm_analyzer")


class RhythmAnalyzer:
    """Analyze narrative rhythm and tension."""

    def analyze(
        self,
        text: str,
        *,
        segment_size: int = 500,
    ) -> dict[str, Any]:
        """Analyze rhythm by dividing text into segments."""
        segments = self._segment_text(text, segment_size)
        rhythm_data: list[dict[str, float]] = []

        for i, seg in enumerate(segments):
            sentences = re.split(r'[。！？]+', seg)
            sentences = [s for s in sentences if s.strip()]
            sent_lengths = [len(s) for s in sentences]
            avg_len = sum(sent_lengths) / max(len(sent_lengths), 1)
            len_variance = sum((l - avg_len)**2 for l in sent_lengths) / max(len(sent_lengths), 1)

            dialogue_density = len(re.findall(r'[""「」]', seg)) / max(len(seg), 1)
            action_words = len(re.findall(r'(?:冲|砍|劈|刺|躲|闪|跑|逃|飞|落|击)', seg))
            action_density = action_words / max(len(seg), 1) * 1000

            excl = seg.count('！')
            tension = (action_density * 30 + excl * 5 +
                       (1 / max(avg_len, 1)) * 100 + dialogue_density * 50)

            rhythm_data.append({
                "segment": i,
                "avg_sentence_length": round(avg_len, 1),
                "sentence_variance": round(len_variance, 1),
                "dialogue_density": round(dialogue_density, 4),
                "action_density": round(action_density, 2),
                "tension_score": round(tension, 1),
            })

        # Compute overall rhythm metrics
        tensions = [r["tension_score"] for r in rhythm_data]
        return {
            "segments": rhythm_data,
            "overall": {
                "avg_tension": round(sum(tensions) / max(len(tensions), 1), 1),
                "max_tension": round(max(tensions, default=0), 1),
                "min_tension": round(min(tensions, default=0), 1),
                "tension_range": round(max(tensions, default=0) - min(tensions, default=0), 1),
                "segment_count": len(rhythm_data),
            },
        }

    @staticmethod
    def _segment_text(text: str, size: int) -> list[str]:
        segments = []
        for i in range(0, len(text), size):
            seg = text[i:i + size]
            if seg.strip():
                segments.append(seg)
        return segments
