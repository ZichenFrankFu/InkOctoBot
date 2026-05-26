"""
Cross-novel pattern mining — identifies common patterns across all novels.

Aggregates novel-level results to find:
  - Recurring writing technique patterns
  - Common chapter design templates
  - Story arc design patterns
"""
from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

logger = logging.getLogger("inkoctobot.reference_ingest.skill_extraction.pattern_miner")

_PATTERN_MINING_PROMPT = """\
你是一位网文研究专家。以下是从{novel_count}本小说中提取的{category_cn}数据汇总。

请分析这些数据，识别出最有价值的共性模式。每个模式应该是一个可复用的"技巧"，
能够指导AI在写作时参考使用。

对于每个识别出的模式，提供：
1. 模式名称（简洁有力）
2. 详细描述（如何使用这个模式）
3. 适用场景
4. 使用要点和注意事项

请以JSON格式输出：
```json
[
  {{
    "pattern_name": "模式名称",
    "subcategory": "子类别",
    "description": "详细描述",
    "when_to_use": "适用场景",
    "key_points": ["要点1", "要点2"],
    "quality_score": 0.0到1.0的质量评分
  }}
]
```

数据汇总：
{data}"""

_CATEGORY_CN = {
    "writing_technique": "写作手法",
    "chapter_design": "章节剧情设计",
    "story_arc": "全文剧情设计",
}


class PatternMiner:
    """Mine cross-novel patterns from aggregated extraction results."""

    def __init__(self, db_path: str | Path, model_router: Any = None):
        self.db_path = str(db_path)
        self.model_router = model_router

    def _conn(self) -> sqlite3.Connection:
        c = sqlite3.connect(self.db_path)
        c.execute("PRAGMA journal_mode=WAL")
        c.row_factory = sqlite3.Row
        return c

    async def mine_all(self) -> dict[str, list[dict]]:
        """Mine patterns for all three categories."""
        results = {}
        for category in ("writing_technique", "chapter_design", "story_arc"):
            logger.info("Mining patterns for: %s", category)
            patterns = await self.mine_patterns(category)
            results[category] = patterns
            logger.info("Found %d patterns for %s", len(patterns), category)
        return results

    async def mine_patterns(self, category: str) -> list[dict]:
        """Mine patterns for a specific category."""
        # Load all novel-level aggregation results
        novel_data = self._load_novel_data()

        if not novel_data:
            logger.warning("No novel data available for pattern mining")
            return []

        if category == "writing_technique":
            return await self._mine_writing_techniques(novel_data)
        elif category == "chapter_design":
            return await self._mine_chapter_designs(novel_data)
        elif category == "story_arc":
            return await self._mine_story_arcs(novel_data)
        else:
            raise ValueError(f"Unknown category: {category}")

    # ── Data loading ──────────────────────────────────────────

    def _load_novel_data(self) -> list[dict]:
        """Load all novel-level aggregation results."""
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT ep.ref_id, ep.result_json, nm.title, nm.author
                   FROM extraction_progress ep
                   JOIN novel_metadata nm ON ep.ref_id = nm.ref_id
                   WHERE ep.phase='novel_aggregate' AND ep.status='completed'""",
            ).fetchall()

        results = []
        for row in rows:
            try:
                data = json.loads(row["result_json"])
                data["ref_id"] = row["ref_id"]
                data["title"] = row["title"]
                data["author"] = row["author"]
                results.append(data)
            except (json.JSONDecodeError, TypeError):
                continue

        return results

    # ── Writing technique mining ──────────────────────────────

    async def _mine_writing_techniques(
        self, novel_data: list[dict],
    ) -> list[dict]:
        """Find recurring writing technique patterns across novels."""
        # Aggregate technique counts across all novels
        global_technique_counter: Counter = Counter()
        technique_novels: dict[str, list[str]] = defaultdict(list)
        technique_examples: dict[str, list[dict]] = defaultdict(list)

        for novel in novel_data:
            fingerprint = novel.get("technique_fingerprint", {})
            title = novel.get("title", "unknown")
            ref_id = novel.get("ref_id", "")

            # Rhetoric types
            rhetoric_dist = fingerprint.get("rhetoric_distribution", {})
            for rtype, count in rhetoric_dist.items():
                key = f"rhetoric:{rtype}"
                global_technique_counter[key] += count
                if title not in technique_novels.get(key, []):
                    technique_novels[key].append(title)

            # LLM-detected techniques
            llm_tech = fingerprint.get("llm_techniques", {})
            for tech_key, tech_data in llm_tech.items():
                global_technique_counter[tech_key] += tech_data.get("count", 0)
                if title not in technique_novels.get(tech_key, []):
                    technique_novels[tech_key].append(title)
                for ex in tech_data.get("examples", [])[:2]:
                    if len(technique_examples[tech_key]) < 5:
                        technique_examples[tech_key].append({
                            "novel": title,
                            **ex,
                        })

        # Build summary for LLM analysis
        summary_data = {
            "total_novels": len(novel_data),
            "top_techniques": [
                {
                    "name": k,
                    "total_occurrences": v,
                    "novel_count": len(technique_novels.get(k, [])),
                    "examples": technique_examples.get(k, [])[:3],
                }
                for k, v in global_technique_counter.most_common(30)
            ],
        }

        # Use LLM to identify high-level patterns
        if self.model_router:
            patterns = await self._llm_mine(
                "writing_technique", len(novel_data), summary_data,
            )
        else:
            # Fallback: create patterns from top techniques directly
            patterns = self._rule_based_patterns(
                "writing_technique", global_technique_counter, technique_novels,
            )

        # Save to DB
        self._save_patterns("writing_technique", patterns, technique_examples)

        return patterns

    # ── Chapter design mining ─────────────────────────────────

    async def _mine_chapter_designs(
        self, novel_data: list[dict],
    ) -> list[dict]:
        """Find recurring chapter structure patterns."""
        opening_counter: Counter = Counter()
        pacing_segments: list[dict] = []
        hook_densities: list[float] = []
        shuangdian_counter: Counter = Counter()

        for novel in novel_data:
            narrative = novel.get("narrative_structure", {})
            title = novel.get("title", "unknown")

            opening_dist = narrative.get("opening_distribution", {})
            for otype, count in opening_dist.items():
                opening_counter[otype] += count

            hook_densities.append(narrative.get("hook_density", 0))

            for sd in narrative.get("shuangdian", []):
                shuangdian_counter[sd.get("type", "unknown")] += 1

            pacing_segments.extend(narrative.get("pacing_segments", []))

        # Also collect LLM chapter structure data
        chapter_structures: list[dict] = []
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT result_json FROM extraction_progress
                   WHERE phase='chapter_extract' AND status='completed'""",
            ).fetchall()
        for row in rows:
            try:
                data = json.loads(row["result_json"])
                struct = data.get("chapter_structure_llm", {})
                if struct:
                    chapter_structures.append(struct)
            except (json.JSONDecodeError, TypeError):
                continue

        # Aggregate opening/ending types from LLM data
        opening_types: Counter = Counter()
        ending_types: Counter = Counter()
        for cs in chapter_structures:
            if cs.get("opening_type"):
                opening_types[cs["opening_type"]] += 1
            if cs.get("ending_type"):
                ending_types[cs["ending_type"]] += 1

        summary_data = {
            "total_novels": len(novel_data),
            "total_chapters_analyzed": len(chapter_structures),
            "opening_patterns": dict(opening_counter.most_common(10)),
            "opening_types_llm": dict(opening_types.most_common(10)),
            "ending_types_llm": dict(ending_types.most_common(10)),
            "avg_hook_density": round(
                sum(hook_densities) / max(len(hook_densities), 1), 3),
            "shuangdian_types": dict(shuangdian_counter.most_common(10)),
        }

        if self.model_router:
            patterns = await self._llm_mine(
                "chapter_design", len(novel_data), summary_data,
            )
        else:
            patterns = self._rule_based_chapter_patterns(summary_data)

        self._save_patterns("chapter_design", patterns, {})
        return patterns

    # ── Story arc mining ──────────────────────────────────────

    async def _mine_story_arcs(
        self, novel_data: list[dict],
    ) -> list[dict]:
        """Find recurring story arc patterns."""
        arc_types: Counter = Counter()
        arc_details: list[dict] = []

        for novel in novel_data:
            story_arc = novel.get("story_arc", {})
            title = novel.get("title", "unknown")
            if not story_arc:
                continue

            arc_struct = story_arc.get("arc_structure", {})
            arc_type = arc_struct.get("type", "unknown")
            arc_types[arc_type] += 1

            arc_details.append({
                "title": title,
                "arc_type": arc_type,
                "stages": arc_struct.get("stages", []),
                "pacing": story_arc.get("pacing_design", {}),
                "unique_techniques": story_arc.get("unique_techniques", []),
            })

        summary_data = {
            "total_novels": len(novel_data),
            "arc_type_distribution": dict(arc_types.most_common()),
            "arc_details_sample": arc_details[:20],  # Sample for LLM
        }

        if self.model_router:
            patterns = await self._llm_mine(
                "story_arc", len(novel_data), summary_data,
            )
        else:
            patterns = [
                {
                    "pattern_name": f"arc:{atype}",
                    "subcategory": "arc_structure",
                    "description": f"{atype}叙事结构，在{count}本小说中使用",
                    "when_to_use": "全文结构规划",
                    "key_points": [],
                    "quality_score": min(count / max(len(novel_data), 1), 1.0),
                }
                for atype, count in arc_types.most_common(10)
            ]

        self._save_patterns("story_arc", patterns, {})
        return patterns

    # ── LLM pattern mining ────────────────────────────────────

    async def _llm_mine(
        self, category: str, novel_count: int, data: dict,
    ) -> list[dict]:
        """Use LLM to identify patterns from aggregated data."""
        prompt = _PATTERN_MINING_PROMPT.format(
            novel_count=novel_count,
            category_cn=_CATEGORY_CN.get(category, category),
            data=json.dumps(data, ensure_ascii=False, indent=2)[:15000],
        )

        try:
            raw = await self.model_router.invoke(
                role="pattern_miner",
                prompt=prompt,
                max_tokens=8192,
                temperature=0.4,
            )
            import re
            for pat in [r"```json\s*([\s\S]*?)```", r"```\s*([\s\S]*?)```"]:
                m = re.search(pat, raw)
                if m:
                    try:
                        result = json.loads(m.group(1))
                        return result if isinstance(result, list) else []
                    except json.JSONDecodeError:
                        continue
            try:
                result = json.loads(raw)
                return result if isinstance(result, list) else []
            except json.JSONDecodeError:
                return []
        except Exception:
            logger.exception("LLM pattern mining failed for %s", category)
            return []

    # ── Fallback rule-based patterns ──────────────────────────

    def _rule_based_patterns(
        self,
        category: str,
        counter: Counter,
        novel_map: dict[str, list[str]],
    ) -> list[dict]:
        """Create patterns from frequency data without LLM."""
        patterns = []
        for key, count in counter.most_common(20):
            novel_count = len(novel_map.get(key, []))
            patterns.append({
                "pattern_name": key,
                "subcategory": key.split(":")[0] if ":" in key else category,
                "description": f"出现{count}次，覆盖{novel_count}本小说",
                "when_to_use": "",
                "key_points": [],
                "quality_score": round(
                    min(novel_count / 10, 1.0) * 0.7
                    + min(count / 100, 1.0) * 0.3,
                    3,
                ),
            })
        return patterns

    def _rule_based_chapter_patterns(self, data: dict) -> list[dict]:
        """Create chapter design patterns without LLM."""
        patterns = []
        for otype, count in data.get("opening_patterns", {}).items():
            patterns.append({
                "pattern_name": f"opening:{otype}",
                "subcategory": "opening",
                "description": f"{otype}开头模式，出现{count}次",
                "when_to_use": "章节开头",
                "key_points": [],
                "quality_score": 0.5,
            })
        return patterns

    # ── Persistence ───────────────────────────────────────────

    def _save_patterns(
        self,
        category: str,
        patterns: list[dict],
        examples: dict[str, list[dict]],
    ) -> None:
        """Save mined patterns to extracted_patterns table."""
        with self._conn() as conn:
            for pat in patterns:
                pid = f"pat_{uuid.uuid4().hex[:12]}"
                name = pat.get("pattern_name", "unknown")
                subcategory = pat.get("subcategory", "")

                # Check for existing pattern with same name+category
                existing = conn.execute(
                    "SELECT pattern_id FROM extracted_patterns "
                    "WHERE category=? AND pattern_name=?",
                    (category, name),
                ).fetchone()

                if existing:
                    conn.execute(
                        """UPDATE extracted_patterns SET
                               description=?, quality_score=?,
                               examples_json=?
                           WHERE pattern_id=?""",
                        (
                            pat.get("description", ""),
                            pat.get("quality_score", 0),
                            json.dumps(
                                examples.get(name, pat.get("key_points", [])),
                                ensure_ascii=False,
                            ),
                            existing["pattern_id"],
                        ),
                    )
                else:
                    conn.execute(
                        """INSERT INTO extracted_patterns
                           (pattern_id, category, subcategory, pattern_name,
                            description, examples_json, quality_score)
                           VALUES (?,?,?,?,?,?,?)""",
                        (
                            pid, category, subcategory, name,
                            pat.get("description", ""),
                            json.dumps(
                                examples.get(name, pat.get("key_points", [])),
                                ensure_ascii=False,
                            ),
                            pat.get("quality_score", 0),
                        ),
                    )

            # Mark pattern_mine phase as completed
            conn.execute(
                """INSERT INTO extraction_progress
                   (ref_id, phase, chapter_num, status, result_json, completed_at)
                   VALUES (?, 'pattern_mine', 0, 'completed', ?, CURRENT_TIMESTAMP)
                   ON CONFLICT(ref_id, phase, chapter_num) DO UPDATE SET
                       status='completed',
                       result_json=excluded.result_json,
                       completed_at=CURRENT_TIMESTAMP""",
                (
                    f"global_{category}",
                    json.dumps({"pattern_count": len(patterns)}, ensure_ascii=False),
                ),
            )
            conn.commit()
