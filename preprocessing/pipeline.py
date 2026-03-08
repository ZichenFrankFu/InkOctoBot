"""
Preprocessing Pipeline — 5-step main entry point for reference work processing.

README §5.1: Orchestrates chapter splitting → character profiling →
style extraction → rhythm analysis → fragment selection → embedding.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from preprocessing.chapter_splitter import split_chapters
from preprocessing.character_profiler import CharacterProfiler
from preprocessing.style_extractor import StyleExtractor
from preprocessing.rhythm_analyzer import RhythmAnalyzer
from preprocessing.fragment_selector import FragmentSelector

logger = logging.getLogger("inkoctobot.preprocessing.pipeline")


class PreprocessingPipeline:
    """Five-step reference work preprocessing pipeline."""

    def __init__(self, vector_store: Any = None):
        self.profiler = CharacterProfiler()
        self.style_extractor = StyleExtractor()
        self.rhythm_analyzer = RhythmAnalyzer()
        self.fragment_selector = FragmentSelector()
        self.vector_store = vector_store

    def process(
        self,
        text: str,
        ref_id: str = "",
        *,
        skip_embedding: bool = False,
    ) -> dict[str, Any]:
        """Run the full 5-step pipeline on a text."""
        results: dict[str, Any] = {"ref_id": ref_id}

        # Step 1: Chapter splitting
        logger.info("Step 1/5: Splitting chapters...")
        chapters = split_chapters(text)
        results["chapters"] = chapters
        results["chapter_count"] = len(chapters)

        # Step 2: Character profiling
        logger.info("Step 2/5: Extracting character profiles...")
        characters = self.profiler.extract_characters(chapters)
        results["characters"] = characters

        # Step 3: Style extraction
        logger.info("Step 3/5: Extracting style fingerprint...")
        style = self.style_extractor.extract(text)
        results["style_fingerprint"] = style

        # Step 4: Rhythm analysis
        logger.info("Step 4/5: Analyzing rhythm...")
        rhythm = self.rhythm_analyzer.analyze(text)
        results["rhythm"] = rhythm

        # Step 5: Fragment extraction
        logger.info("Step 5/5: Extracting style fragments...")
        sentence_templates = self.fragment_selector.extract_sentence_templates(text)
        paragraph_templates = self.fragment_selector.extract_paragraph_templates(text)
        results["sentence_templates"] = len(sentence_templates)
        results["paragraph_templates"] = len(paragraph_templates)

        # Optional: embed into vector store
        if self.vector_store and not skip_embedding:
            self._embed_fragments(ref_id, sentence_templates, chapters)

        logger.info("Preprocessing complete: %d chapters, %d characters, %d templates",
                     len(chapters), len(characters), len(sentence_templates))
        return results

    def _embed_fragments(
        self, ref_id: str,
        templates: list[dict], chapters: list[dict],
    ) -> None:
        """Embed extracted fragments into ChromaDB."""
        for i, tmpl in enumerate(templates[:200]):
            self.vector_store.add(
                doc_id=f"{ref_id}_sent_{i}",
                text=tmpl["original"],
                metadata={
                    "ref_id": ref_id,
                    "type": "sentence_template",
                    "sentence_type": tmpl["type"],
                },
            )
        for ch in chapters:
            content = ch.get("content", "")[:500]
            if content:
                self.vector_store.add(
                    doc_id=f"{ref_id}_ch_{ch['chapter_num']}",
                    text=content,
                    metadata={
                        "ref_id": ref_id,
                        "type": "chapter_content",
                        "chapter_num": ch["chapter_num"],
                    },
                )
