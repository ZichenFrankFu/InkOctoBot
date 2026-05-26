"""
Skill Extraction Orchestrator — coordinates the full extraction pipeline.

Phases:
  0. Ingest & Clean  (novel_ingester)
  1. Chapter-Level Extraction  (chapter_extractor)
  2. Novel-Level Aggregation  (novel_aggregator)
  3. Cross-Novel Pattern Mining  (pattern_miner)
  4. Skill Emission  (skill_emitter)

Supports:
  - Resume from any phase
  - Per-novel or full-corpus execution
  - Progress tracking via extraction_progress table
"""
from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path
from typing import Any

logger = logging.getLogger("inkoctobot.reference_ingest.skill_extraction.orchestrator")


class SkillExtractionOrchestrator:
    """Orchestrate the full novel skill extraction pipeline."""

    def __init__(
        self,
        db_path: str | Path,
        corpus_dir: str | Path | None = None,
        model_router: Any = None,
        vector_store: Any = None,
    ):
        self.db_path = str(db_path)
        self.corpus_dir = Path(corpus_dir) if corpus_dir else Path("data/novel_corpus")
        self.model_router = model_router
        self.vector_store = vector_store

    def _conn(self) -> sqlite3.Connection:
        c = sqlite3.connect(self.db_path)
        c.execute("PRAGMA journal_mode=WAL")
        c.row_factory = sqlite3.Row
        return c

    # ── Main entry point ──────────────────────────────────────

    async def run(
        self,
        *,
        resume: bool = True,
        novels: list[str] | None = None,
        phases: list[str] | None = None,
    ) -> dict[str, Any]:
        """
        Run the extraction pipeline.

        Args:
            resume: If True, skip already-completed steps.
            novels: List of novel titles to process (None = all).
            phases: List of phases to run (None = all).
                    Options: 'clean', 'chapter', 'novel', 'pattern', 'emit'
        """
        all_phases = ["clean", "chapter", "novel", "pattern", "emit"]
        active_phases = phases or all_phases

        results: dict[str, Any] = {}

        # Phase 0: Ingest & Clean
        if "clean" in active_phases:
            logger.info("═══ Phase 0: Ingest & Clean ═══")
            results["clean"] = self._run_ingest()

        # Get novel list
        novel_refs = self._get_novel_refs(novels)
        logger.info("Processing %d novels", len(novel_refs))

        # Phase 1: Chapter-Level Extraction
        if "chapter" in active_phases:
            logger.info("═══ Phase 1: Chapter-Level Extraction ═══")
            results["chapter"] = await self._run_chapter_extraction(
                novel_refs, resume=resume,
            )

        # Phase 2: Novel-Level Aggregation
        if "novel" in active_phases:
            logger.info("═══ Phase 2: Novel-Level Aggregation ═══")
            results["novel"] = await self._run_novel_aggregation(
                novel_refs, resume=resume,
            )

        # Phase 3: Cross-Novel Pattern Mining
        if "pattern" in active_phases:
            logger.info("═══ Phase 3: Cross-Novel Pattern Mining ═══")
            results["pattern"] = await self._run_pattern_mining()

        # Phase 4: Skill Emission
        if "emit" in active_phases:
            logger.info("═══ Phase 4: Skill Emission ═══")
            results["emit"] = self._run_skill_emission()

        logger.info("Pipeline complete: %s", json.dumps(results, ensure_ascii=False))
        return results

    # ── Phase implementations ─────────────────────────────────

    def _run_ingest(self) -> dict[str, Any]:
        """Phase 0: Ingest and clean novels."""
        from reference_ingest.novel_ingester import NovelIngester

        ingester = NovelIngester(self.db_path, self.corpus_dir)
        ingest_results = ingester.ingest_all()

        needs_review = []
        for r in ingest_results:
            needs_review.extend(r.needs_review)

        return {
            "ingested": len(ingest_results),
            "titles": [r.title for r in ingest_results],
            "needs_review": len(needs_review),
        }

    async def _run_chapter_extraction(
        self, novel_refs: list[dict], *, resume: bool = True,
    ) -> dict[str, Any]:
        """Phase 1: Extract from each chapter of each novel."""
        from reference_ingest.skill_extraction.chapter_extractor import ChapterExtractor

        extractor = ChapterExtractor(self.db_path, self.model_router)

        total_processed = 0
        total_skipped = 0
        errors = 0

        for ref in novel_refs:
            ref_id = ref["ref_id"]
            title = ref["title"]
            logger.info("Extracting chapters: %s (%s)", title, ref_id)

            try:
                result = await extractor.extract_novel(ref_id, resume=resume)
                total_processed += result["processed"]
                total_skipped += result["skipped"]
            except Exception:
                logger.exception("Failed novel: %s", title)
                errors += 1

        return {
            "novels": len(novel_refs),
            "chapters_processed": total_processed,
            "chapters_skipped": total_skipped,
            "errors": errors,
        }

    async def _run_novel_aggregation(
        self, novel_refs: list[dict], *, resume: bool = True,
    ) -> dict[str, Any]:
        """Phase 2: Aggregate chapter results into novel-level analysis."""
        from reference_ingest.skill_extraction.novel_aggregator import NovelAggregator

        aggregator = NovelAggregator(self.db_path, self.model_router)
        aggregated = 0
        skipped = 0
        errors = 0

        for ref in novel_refs:
            ref_id = ref["ref_id"]
            title = ref["title"]

            # Check if already done
            if resume:
                with self._conn() as conn:
                    done = conn.execute(
                        "SELECT 1 FROM extraction_progress "
                        "WHERE ref_id=? AND phase='novel_aggregate' AND status='completed'",
                        (ref_id,),
                    ).fetchone()
                    if done:
                        skipped += 1
                        continue

            logger.info("Aggregating: %s", title)
            try:
                await aggregator.aggregate(ref_id)
                aggregated += 1
            except Exception:
                logger.exception("Failed aggregation: %s", title)
                errors += 1

        return {
            "aggregated": aggregated,
            "skipped": skipped,
            "errors": errors,
        }

    async def _run_pattern_mining(self) -> dict[str, Any]:
        """Phase 3: Mine patterns across all novels."""
        from reference_ingest.skill_extraction.pattern_miner import PatternMiner

        miner = PatternMiner(self.db_path, self.model_router)
        patterns = await miner.mine_all()

        return {
            category: len(pats)
            for category, pats in patterns.items()
        }

    def _run_skill_emission(self) -> dict[str, Any]:
        """Phase 4: Generate skill files and populate vector store."""
        from reference_ingest.skill_extraction.skill_emitter import SkillEmitter

        emitter = SkillEmitter(
            self.db_path,
            vector_store=self.vector_store,
        )
        counts = emitter.emit_all()

        return {"skills_emitted": counts}

    # ── Helpers ───────────────────────────────────────────────

    def _get_novel_refs(self, titles: list[str] | None = None) -> list[dict]:
        """Get novel reference IDs, optionally filtered by title."""
        with self._conn() as conn:
            if titles:
                placeholders = ",".join("?" * len(titles))
                rows = conn.execute(
                    f"SELECT ref_id, title FROM novel_metadata "
                    f"WHERE title IN ({placeholders})",
                    titles,
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT ref_id, title FROM novel_metadata ORDER BY title",
                ).fetchall()
        return [dict(r) for r in rows]

    def get_status(self) -> dict[str, Any]:
        """Get overall pipeline status."""
        with self._conn() as conn:
            # Novel counts
            total_novels = conn.execute(
                "SELECT COUNT(*) FROM novel_metadata",
            ).fetchone()[0]

            # Phase completion counts
            phase_status = {}
            for phase in ("clean", "chapter_extract", "novel_aggregate", "pattern_mine"):
                rows = conn.execute(
                    "SELECT status, COUNT(*) as cnt FROM extraction_progress "
                    "WHERE phase=? GROUP BY status",
                    (phase,),
                ).fetchall()
                phase_status[phase] = {r["status"]: r["cnt"] for r in rows}

            # Pattern counts
            pattern_counts = conn.execute(
                "SELECT category, COUNT(*) as cnt FROM extracted_patterns "
                "GROUP BY category",
            ).fetchall()

            # Emitted skills
            emitted = conn.execute(
                "SELECT COUNT(*) FROM extracted_patterns WHERE skill_emitted=1",
            ).fetchone()[0]

        return {
            "total_novels": total_novels,
            "phases": phase_status,
            "patterns": {r["category"]: r["cnt"] for r in pattern_counts},
            "skills_emitted": emitted,
        }
