"""
Repetition Detect skill — rule-based repetition detection.

Detects word-level, phrase-level, and structural repetition
without any LLM calls.
"""
from __future__ import annotations

import logging
import re
from collections import Counter
from typing import Any

from agents.base_skill import BaseSkill, SkillMeta

logger = logging.getLogger("inkoctobot.skills.repetition_detect")

_PHRASE_MIN_LEN = 4
_PHRASE_THRESHOLD = 3


class Skill(BaseSkill):
    """Rule-based repetition detector — overrides execute() to skip LLM."""

    def meta(self) -> SkillMeta:
        return SkillMeta(
            name="repetition_detect",
            display_name="重复检测",
            description=(
                "基于规则的重复模式检测，包括词汇级、短语级和结构级重复分析，"
                "无需LLM调用。"
            ),
            version="1.0.0",
            model_role="none",
            tags=["evaluation", "quality", "repetition", "rule-based"],
            permissions=[],
            input_schema={
                "type": "object",
                "required": ["text"],
                "properties": {
                    "text": {"type": "string"},
                },
            },
            output_schema={
                "type": "object",
                "required": ["score", "issues", "has_issues"],
                "properties": {
                    "score": {"type": "number"},
                    "issues": {"type": "array"},
                    "has_issues": {"type": "boolean"},
                },
            },
        )

    async def build_prompt(self, inputs: dict[str, Any]) -> str:
        # Not used — execute() is overridden.
        return ""

    async def parse_output(self, raw: str) -> dict[str, Any]:
        # Not used — execute() is overridden.
        return {}

    async def execute(
        self,
        inputs: dict[str, Any],
        model_router: Any = None,
    ) -> dict[str, Any]:
        """Run rule-based repetition detection directly (no LLM)."""
        text: str = inputs.get("text", "")
        issues: list[dict[str, Any]] = []

        # 1. Sentence-start repetition
        sentence_starts = self._check_sentence_starts(text)
        if sentence_starts:
            issues.append({"type": "sentence_start", "items": sentence_starts})

        # 2. Repeated phrases
        phrases = self._check_repeated_phrases(text)
        if phrases:
            issues.append({"type": "repeated_phrase", "items": phrases})

        # 3. Structural repetition
        structural = self._check_structural_repetition(text)
        if structural:
            issues.append({"type": "structural", "description": structural})

        score = max(0, 100 - len(issues) * 15)
        return {"score": score, "issues": issues, "has_issues": len(issues) > 0}

    # ── Internal helpers ────────────────────────────────────────

    @staticmethod
    def _check_sentence_starts(text: str) -> list[dict[str, Any]]:
        sentences = re.split(r"[。！？\n]+", text)
        starts = [s.strip()[:6] for s in sentences if len(s.strip()) > 6]
        counter = Counter(starts)
        return [
            {"start": s, "count": c}
            for s, c in counter.items()
            if c >= _PHRASE_THRESHOLD
        ]

    @staticmethod
    def _check_repeated_phrases(text: str) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for length in range(_PHRASE_MIN_LEN, min(20, len(text) // 4)):
            phrases: Counter[str] = Counter()
            for i in range(len(text) - length):
                phrase = text[i : i + length]
                if re.search(r"[。！？\n]", phrase):
                    continue
                phrases[phrase] += 1
            for phrase, count in phrases.items():
                if count >= _PHRASE_THRESHOLD and not phrase.isspace():
                    results.append({"phrase": phrase, "count": count})

        # Deduplicate — keep longest variant
        results.sort(key=lambda x: len(x["phrase"]), reverse=True)
        seen: set[str] = set()
        deduped: list[dict[str, Any]] = []
        for r in results:
            if not any(r["phrase"] in s for s in seen):
                deduped.append(r)
                seen.add(r["phrase"])
        return deduped[:10]

    @staticmethod
    def _check_structural_repetition(text: str) -> str:
        paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
        if len(paragraphs) < 3:
            return ""
        lens = [len(p) for p in paragraphs]
        avg_len = sum(lens) / len(lens) if lens else 0
        if avg_len > 0:
            variance = sum((ln - avg_len) ** 2 for ln in lens) / len(lens)
            if variance < (avg_len * 0.1) ** 2 and len(paragraphs) > 5:
                return "段落长度过于一致，可能显得机械化"
        return ""
