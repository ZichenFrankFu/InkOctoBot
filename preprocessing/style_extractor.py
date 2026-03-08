"""
PROSE-inspired Style Extractor — iterative style convergence extraction.

README Appendix A (PROSE paper): Extracts writing style features from
reference texts through statistical analysis.
"""
from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger("inkoctobot.preprocessing.style_extractor")


class StyleExtractor:
    """Extract style fingerprint from text."""

    def extract(self, text: str) -> dict[str, Any]:
        """Compute a style fingerprint from the given text."""
        sentences = re.split(r'[。！？…]+', text)
        sentences = [s.strip() for s in sentences if len(s.strip()) > 1]

        dialogues = re.findall(r'[""「](.+?)[""」]', text)
        paragraphs = [p.strip() for p in text.split("\n") if p.strip()]

        # Sentence-level features
        sent_lengths = [len(s) for s in sentences]
        avg_sent = sum(sent_lengths) / max(len(sent_lengths), 1)

        # Dialogue features
        dialogue_ratio = sum(len(d) for d in dialogues) / max(len(text), 1)

        # Paragraph features
        para_lengths = [len(p) for p in paragraphs]
        avg_para = sum(para_lengths) / max(len(para_lengths), 1)

        # Punctuation density
        punct_count = len(re.findall(r'[，。！？；：、…—]', text))
        punct_density = punct_count / max(len(text), 1)

        # Rhetorical devices
        simile_count = len(re.findall(r'(?:如同|宛如|好似|像是|犹如|仿佛).{2,20}(?:般|一样|似的)', text))
        metaphor_count = len(re.findall(r'(?:是|化作|变成).{2,15}的[^。]{2,10}', text))

        # Sentence type distribution
        exclamations = len(re.findall(r'！', text))
        questions = len(re.findall(r'？', text))
        ellipsis = len(re.findall(r'[…]+', text))

        # Inner monologue detection
        inner_mono = len(re.findall(r'(?:心中|心想|心里|暗想|暗道|内心)', text))

        return {
            "avg_sentence_length": round(avg_sent, 1),
            "sentence_count": len(sentences),
            "dialogue_ratio": round(dialogue_ratio, 3),
            "dialogue_count": len(dialogues),
            "avg_paragraph_length": round(avg_para, 1),
            "paragraph_count": len(paragraphs),
            "punctuation_density": round(punct_density, 4),
            "simile_count": simile_count,
            "metaphor_count": metaphor_count,
            "exclamation_ratio": round(exclamations / max(len(sentences), 1), 3),
            "question_ratio": round(questions / max(len(sentences), 1), 3),
            "ellipsis_count": ellipsis,
            "inner_monologue_density": round(inner_mono / max(len(paragraphs), 1), 3),
            "total_chars": len(text),
        }

    def compare(self, profile_a: dict, profile_b: dict) -> dict[str, Any]:
        """Compare two style profiles and report deviations."""
        deviations = {}
        for key in profile_a:
            if key in profile_b and isinstance(profile_a[key], (int, float)):
                va, vb = profile_a[key], profile_b[key]
                ref = max(abs(va), abs(vb), 1)
                dev = abs(va - vb) / ref
                if dev > 0.3:
                    deviations[key] = {
                        "a": va, "b": vb, "deviation": round(dev, 3)
                    }
        return {
            "similar": len(deviations) == 0,
            "deviations": deviations,
            "deviation_count": len(deviations),
        }
