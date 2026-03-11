"""Writing style extraction skill — extracts style features from text
using rule-based NLP statistics. Does not require an LLM call."""

from __future__ import annotations

import math
import re
import logging
from typing import Any

from agents.base_skill import BaseSkill, SkillMeta

logger = logging.getLogger("inkoctobot.skills.style_extract")

# Sentence-ending punctuation (Chinese + Western)
_SENT_END = re.compile(r"[。！？!?.…]+")
# Dialogue patterns: Chinese quotation marks or Western quotes
_DIALOGUE = re.compile(r"[「」『』""'']")
_DIALOGUE_BLOCK = re.compile(r'[\u300c\u300e\u201c\u2018][^\u300d\u300f\u201d\u2019]*[\u300d\u300f\u201d\u2019]|"[^"]*"')
# Chinese/English word-level tokenisation (simple: consecutive CJK chars
# each count as one word; consecutive latin chars count as one word)
_TOKEN = re.compile(r"[\u4e00-\u9fff]|[a-zA-Z]+")
# Paragraph split
_PARA_SPLIT = re.compile(r"\n\s*\n|\n")


class Skill(BaseSkill):
    """Extract writing style features via rule-based NLP statistics."""

    def meta(self) -> SkillMeta:
        return SkillMeta(
            name="style_extract",
            display_name="写作风格提取",
            description=(
                "提取文本的写作风格特征，包括句长统计、对话比例、用词丰富度、"
                "段落结构等，基于规则与NLP统计。"
            ),
            version="1.0.0",
            model_role="analyzer",
            max_tokens=1024,
            temperature=0.0,
            tags=["analysis", "style", "extraction", "rule-based"],
            permissions=["read_style"],
            input_schema={
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "待分析的文本内容"},
                },
                "required": ["text"],
            },
            output_schema={
                "type": "object",
                "properties": {
                    "sentence_stats": {"type": "object"},
                    "dialogue_ratio": {"type": "number"},
                    "vocabulary": {"type": "object"},
                    "paragraph_stats": {"type": "object"},
                    "punctuation_profile": {"type": "object"},
                },
                "required": [
                    "sentence_stats",
                    "dialogue_ratio",
                    "vocabulary",
                    "paragraph_stats",
                    "punctuation_profile",
                ],
            },
        )

    # ── Rule-based: override execute to skip LLM call ────────────

    async def execute(
        self,
        inputs: dict[str, Any],
        model_router: Any = None,
    ) -> dict[str, Any]:
        """Compute style features directly — no LLM needed."""
        text = inputs.get("text", "")
        return self._analyse(text)

    async def build_prompt(self, inputs: dict[str, Any]) -> str:
        # Not used in normal flow, but required by ABC.
        return inputs.get("text", "")

    async def parse_output(self, raw: str) -> dict[str, Any]:
        # Fallback: if someone calls via the standard LLM pipeline,
        # just run the rule-based analysis on the raw text.
        return self._analyse(raw)

    # ── Core analysis ────────────────────────────────────────────

    def _analyse(self, text: str) -> dict[str, Any]:
        sentence_stats = self._sentence_stats(text)
        dialogue_ratio = self._dialogue_ratio(text)
        vocabulary = self._vocabulary(text)
        paragraph_stats = self._paragraph_stats(text)
        punctuation_profile = self._punctuation_profile(text)
        return {
            "sentence_stats": sentence_stats,
            "dialogue_ratio": dialogue_ratio,
            "vocabulary": vocabulary,
            "paragraph_stats": paragraph_stats,
            "punctuation_profile": punctuation_profile,
        }

    # ── Helpers ──────────────────────────────────────────────────

    @staticmethod
    def _sentence_stats(text: str) -> dict[str, Any]:
        sentences = [s.strip() for s in _SENT_END.split(text) if s.strip()]
        if not sentences:
            return {
                "count": 0,
                "avg_length": 0.0,
                "min_length": 0,
                "max_length": 0,
                "std_dev": 0.0,
            }
        lengths = [len(s) for s in sentences]
        avg = sum(lengths) / len(lengths)
        variance = sum((l - avg) ** 2 for l in lengths) / len(lengths)
        return {
            "count": len(sentences),
            "avg_length": round(avg, 2),
            "min_length": min(lengths),
            "max_length": max(lengths),
            "std_dev": round(math.sqrt(variance), 2),
        }

    @staticmethod
    def _dialogue_ratio(text: str) -> float:
        if not text:
            return 0.0
        dialogue_chars = sum(len(m.group()) for m in _DIALOGUE_BLOCK.finditer(text))
        total = len(text.strip())
        if total == 0:
            return 0.0
        return round(dialogue_chars / total, 4)

    @staticmethod
    def _vocabulary(text: str) -> dict[str, Any]:
        tokens = _TOKEN.findall(text)
        if not tokens:
            return {
                "unique_words": 0,
                "total_words": 0,
                "ttr": 0.0,
                "hapax_ratio": 0.0,
            }
        total = len(tokens)
        freq: dict[str, int] = {}
        for t in tokens:
            freq[t] = freq.get(t, 0) + 1
        unique = len(freq)
        hapax = sum(1 for v in freq.values() if v == 1)
        return {
            "unique_words": unique,
            "total_words": total,
            "ttr": round(unique / total, 4) if total else 0.0,
            "hapax_ratio": round(hapax / total, 4) if total else 0.0,
        }

    @staticmethod
    def _paragraph_stats(text: str) -> dict[str, Any]:
        paragraphs = [p.strip() for p in _PARA_SPLIT.split(text) if p.strip()]
        if not paragraphs:
            return {"count": 0, "avg_length": 0.0}
        lengths = [len(p) for p in paragraphs]
        return {
            "count": len(paragraphs),
            "avg_length": round(sum(lengths) / len(lengths), 2),
        }

    @staticmethod
    def _punctuation_profile(text: str) -> dict[str, Any]:
        total = len(text) if text else 1  # avoid division by zero
        exclamation = text.count("！") + text.count("!")
        question = text.count("？") + text.count("?")
        ellipsis = text.count("…") + text.count("...")
        comma = text.count("，") + text.count(",")
        return {
            "exclamation_ratio": round(exclamation / total, 4),
            "question_ratio": round(question / total, 4),
            "ellipsis_ratio": round(ellipsis / total, 4),
            "comma_density": round(comma / total, 4),
        }
