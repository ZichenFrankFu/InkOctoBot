"""
ZeroStylus Fragment Selector — sentence/paragraph template extraction.

README Appendix A (ZeroStylus paper): Extracts sentence-level and
paragraph-level structure templates from reference texts for
style transfer learning.
"""
from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger("inkoctobot.preprocessing.fragment_selector")


class FragmentSelector:
    """Extract sentence and paragraph templates from text."""

    def extract_sentence_templates(
        self, text: str, *, min_length: int = 10, max_length: int = 100,
    ) -> list[dict[str, Any]]:
        """
        Extract sentence-level structure templates.

        Replaces specific nouns/names with placeholders to create reusable templates.
        """
        sentences = re.split(r'(?<=[。！？])', text)
        templates = []

        for sent in sentences:
            sent = sent.strip()
            if len(sent) < min_length or len(sent) > max_length:
                continue

            # Classify sentence type
            sent_type = self._classify_sentence(sent)
            # Create template by abstracting specifics
            template = self._abstract_sentence(sent)

            templates.append({
                "original": sent,
                "template": template,
                "type": sent_type,
                "length": len(sent),
            })

        logger.info("Extracted %d sentence templates", len(templates))
        return templates

    def extract_paragraph_templates(
        self, text: str, *, min_sentences: int = 2,
    ) -> list[dict[str, Any]]:
        """Extract paragraph-level structure templates."""
        paragraphs = [p.strip() for p in text.split("\n") if p.strip()]
        templates = []

        for para in paragraphs:
            sentences = re.split(r'(?<=[。！？])', para)
            sentences = [s.strip() for s in sentences if s.strip()]
            if len(sentences) < min_sentences:
                continue

            types = [self._classify_sentence(s) for s in sentences]
            structure = " → ".join(types)

            templates.append({
                "structure": structure,
                "sentence_types": types,
                "sentence_count": len(sentences),
                "sample": para[:200],
            })

        return templates

    @staticmethod
    def _classify_sentence(sent: str) -> str:
        """Classify sentence type."""
        if re.search(r'[""「」]', sent):
            return "dialogue"
        if re.search(r'(?:心中|心想|暗想|内心)', sent):
            return "inner_thought"
        if re.search(r'(?:冲|砍|劈|刺|躲|闪|跑|抬手|挥|握)', sent):
            return "action"
        if re.search(r'(?:天空|大地|阳光|月光|风|雨|雪|云|山|水|树)', sent):
            return "environment"
        if re.search(r'(?:脸上|眼中|嘴角|声音|表情|神色)', sent):
            return "expression"
        return "narration"

    @staticmethod
    def _abstract_sentence(sent: str) -> str:
        """Abstract a sentence into a structural template."""
        result = sent
        # Replace character names (2-3 char Chinese names)
        result = re.sub(r'[\u4e00-\u9fa5]{2,3}(?=说|道|笑|叹)', '[角色]', result)
        # Replace numbers
        result = re.sub(r'\d+', '[N]', result)
        return result
