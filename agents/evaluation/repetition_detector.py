"""
Repetition Detector — identifies repetitive expressions and patterns.

Detects word-level, phrase-level, and structural repetition.
"""
from __future__ import annotations

import logging
import re
from collections import Counter
from typing import Any

logger = logging.getLogger("inkoctobot.agents.evaluation.repetition_detector")


class RepetitionDetector:
    """Rule-based repetition detection (no LLM needed)."""

    def __init__(self, *, phrase_min_len: int = 4, phrase_threshold: int = 3):
        self.phrase_min_len = phrase_min_len
        self.phrase_threshold = phrase_threshold

    def detect(self, text: str) -> dict[str, Any]:
        """Detect repetitions in text."""
        issues: list[dict[str, Any]] = []

        # Sentence-start repetition
        sentence_starts = self._check_sentence_starts(text)
        if sentence_starts:
            issues.append({"type": "sentence_start", "items": sentence_starts})

        # Repeated phrases
        phrases = self._check_repeated_phrases(text)
        if phrases:
            issues.append({"type": "repeated_phrase", "items": phrases})

        # Paragraph structure repetition
        structural = self._check_structural_repetition(text)
        if structural:
            issues.append({"type": "structural", "description": structural})

        score = max(0, 100 - len(issues) * 15)
        return {"score": score, "issues": issues, "has_issues": len(issues) > 0}

    def _check_sentence_starts(self, text: str) -> list[dict]:
        sentences = re.split(r'[。！？\n]+', text)
        starts = [s.strip()[:6] for s in sentences if len(s.strip()) > 6]
        counter = Counter(starts)
        return [
            {"start": s, "count": c}
            for s, c in counter.items() if c >= self.phrase_threshold
        ]

    def _check_repeated_phrases(self, text: str) -> list[dict]:
        results = []
        for length in range(self.phrase_min_len, min(20, len(text) // 4)):
            phrases: Counter[str] = Counter()
            for i in range(len(text) - length):
                phrase = text[i:i + length]
                if re.search(r'[。！？\n]', phrase):
                    continue
                phrases[phrase] += 1
            for phrase, count in phrases.items():
                if count >= self.phrase_threshold and not phrase.isspace():
                    results.append({"phrase": phrase, "count": count})
        # Deduplicate (keep longest)
        results.sort(key=lambda x: len(x["phrase"]), reverse=True)
        seen_phrases: set[str] = set()
        deduped = []
        for r in results:
            if not any(r["phrase"] in s for s in seen_phrases):
                deduped.append(r)
                seen_phrases.add(r["phrase"])
        return deduped[:10]

    def _check_structural_repetition(self, text: str) -> str:
        paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
        if len(paragraphs) < 3:
            return ""
        lens = [len(p) for p in paragraphs]
        avg_len = sum(lens) / len(lens) if lens else 0
        if avg_len > 0:
            variance = sum((l - avg_len) ** 2 for l in lens) / len(lens)
            if variance < (avg_len * 0.1) ** 2 and len(paragraphs) > 5:
                return "段落长度过于一致，可能显得机械化"
        return ""
