"""
Novel-level aggregation — aggregates chapter-level extraction results
into whole-novel analysis.

Produces:
  - Writing technique fingerprint (frequency + quality distribution)
  - Full-novel plot arc analysis (using cloud LLM on chapter summaries)
  - Character development trajectories
"""
from __future__ import annotations

import json
import logging
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any

from analysis.feature_extraction.narrative_extractor import (
    extract_narrative,
    extract_rhythm,
)

logger = logging.getLogger("inkoctobot.preprocessing.skill_extraction.novel_aggregator")

_STORY_ARC_PROMPT = """\
你是一位资深小说编辑和叙事学专家。以下是一本小说所有章节的摘要。
请分析这本小说的全局剧情设计。

分析以下方面：
1. 整体叙事弧线结构（三幕式/四幕式/英雄之旅/起承转合/其他，并说明每段的章节范围）
2. 主线与副线：主线是什么，有哪些副线，它们如何交织
3. 角色成长轨迹：主角和关键配角的成长/变化曲线
4. 伏笔-揭示链：哪些伏笔在后续被揭示，伏笔的密度和间隔
5. 节奏设计：高潮的分布，张弛交替的规律
6. 独特的剧情设计手法：任何值得学习的剧情编排技巧

请以JSON格式输出：
```json
{{
  "arc_structure": {{
    "type": "结构类型",
    "stages": [
      {{"name": "阶段名", "chapter_range": "ch X - ch Y", "description": "描述"}}
    ]
  }},
  "main_plot": "主线描述",
  "subplots": [
    {{"name": "副线名", "description": "描述", "intersection_points": ["与主线交汇点"]}}
  ],
  "character_arcs": [
    {{"name": "角色名", "arc_type": "弧线类型", "key_moments": ["关键转折"]}}
  ],
  "foreshadow_chains": [
    {{"setup": "伏笔", "setup_chapter": N, "payoff": "揭示", "payoff_chapter": N}}
  ],
  "pacing_design": {{
    "climax_positions": ["章节位置百分比"],
    "rhythm_pattern": "节奏模式描述"
  }},
  "unique_techniques": [
    {{"technique": "手法名", "description": "描述", "example_chapters": [N]}}
  ]
}}
```

小说标题：{title}
总章节数：{total_chapters}

章节摘要列表：
{summaries}"""


class NovelAggregator:
    """Aggregate chapter-level results into novel-level analysis."""

    def __init__(self, db_path: str | Path, model_router: Any = None):
        self.db_path = str(db_path)
        self.model_router = model_router

    def _conn(self) -> sqlite3.Connection:
        c = sqlite3.connect(self.db_path)
        c.execute("PRAGMA journal_mode=WAL")
        c.row_factory = sqlite3.Row
        return c

    async def aggregate(self, ref_id: str) -> dict[str, Any]:
        """
        Run novel-level aggregation for a completed chapter extraction.

        Returns dict with keys:
          technique_fingerprint, narrative_structure, story_arc_llm
        """
        # Check if chapter extraction is complete
        with self._conn() as conn:
            meta = conn.execute(
                "SELECT * FROM novel_metadata WHERE ref_id=?", (ref_id,),
            ).fetchone()
            if not meta:
                raise ValueError(f"Novel not found: {ref_id}")

            total_chapters = meta["total_chapters"]

            # Load all chapter extraction results
            rows = conn.execute(
                "SELECT chapter_num, result_json FROM extraction_progress "
                "WHERE ref_id=? AND phase='chapter_extract' AND status='completed' "
                "ORDER BY chapter_num",
                (ref_id,),
            ).fetchall()

        if len(rows) < total_chapters * 0.8:
            logger.warning(
                "Only %d/%d chapters completed for %s, proceeding anyway",
                len(rows), total_chapters, ref_id,
            )

        # Parse all chapter results
        chapter_results = []
        for row in rows:
            try:
                data = json.loads(row["result_json"])
                data["chapter_num"] = row["chapter_num"]
                chapter_results.append(data)
            except (json.JSONDecodeError, TypeError):
                continue

        result: dict[str, Any] = {}

        # 1. Writing technique fingerprint
        result["technique_fingerprint"] = self._aggregate_techniques(chapter_results)

        # 2. Rule-based narrative structure
        result["narrative_structure"] = self._aggregate_narrative(chapter_results)

        # 3. LLM-based full story arc analysis
        if self.model_router:
            result["story_arc"] = await self._analyze_story_arc(
                meta["title"], total_chapters, chapter_results,
            )
        else:
            result["story_arc"] = {}

        # Save to DB
        self._save_result(ref_id, result)

        return result

    # ── Technique aggregation ─────────────────────────────────

    def _aggregate_techniques(
        self, chapter_results: list[dict],
    ) -> dict[str, Any]:
        """Aggregate writing techniques across all chapters."""
        # Count rule-based rhetoric types
        rhetoric_counter: Counter = Counter()
        total_rhetoric = 0

        # Count LLM-detected technique types
        llm_technique_counter: Counter = Counter()
        technique_examples: dict[str, list] = {}

        for ch in chapter_results:
            ch_num = ch.get("chapter_num", 0)

            # Rule-based
            rule_data = ch.get("writing_techniques_rule", {})
            rhetoric = rule_data.get("rhetoric", {})
            dist = rhetoric.get("distribution", {})
            for rtype, count in dist.items():
                rhetoric_counter[rtype] += count
                total_rhetoric += count

            # LLM-based
            llm_techniques = ch.get("writing_techniques_llm", [])
            for tech in llm_techniques:
                if not isinstance(tech, dict):
                    continue
                ttype = tech.get("technique_type", "unknown")
                tname = tech.get("technique_name", "unknown")
                key = f"{ttype}:{tname}"
                llm_technique_counter[key] += 1

                if key not in technique_examples:
                    technique_examples[key] = []
                if len(technique_examples[key]) < 3:
                    technique_examples[key].append({
                        "chapter": ch_num,
                        "description": tech.get("description", ""),
                        "excerpt": tech.get("excerpt", "")[:200],
                    })

        return {
            "rhetoric_distribution": dict(rhetoric_counter.most_common()),
            "rhetoric_total": total_rhetoric,
            "rhetoric_density_per_chapter": round(
                total_rhetoric / max(len(chapter_results), 1), 2,
            ),
            "llm_techniques": {
                k: {"count": v, "examples": technique_examples.get(k, [])}
                for k, v in llm_technique_counter.most_common(30)
            },
            "signature_techniques": [
                k for k, v in llm_technique_counter.most_common(5)
                if v >= max(len(chapter_results) * 0.1, 3)
            ],
        }

    # ── Narrative structure ───────────────────────────────────

    def _aggregate_narrative(
        self, chapter_results: list[dict],
    ) -> dict[str, Any]:
        """Build narrative structure from rule-based chapter data."""
        # Build chapter-like dicts for narrative_extractor functions
        pseudo_chapters = []
        tensions = []
        hook_counts = []
        openings: Counter = Counter()
        shuangdian_all: list[dict] = []

        for ch in chapter_results:
            ch_num = ch.get("chapter_num", 0)
            rule_data = ch.get("chapter_structure_rule", {})

            t = rule_data.get("tension", 0)
            tensions.append(t)
            hook_counts.append(rule_data.get("hook_count", 0))
            openings[rule_data.get("opening_pattern", "other")] += 1

            for sd_type in rule_data.get("shuangdian_types", []):
                shuangdian_all.append({"chapter": ch_num, "type": sd_type})

            pseudo_chapters.append({"content": "", "chapter_num": ch_num})

        # Compute rhythm from tensions
        avg_tension = sum(tensions) / max(len(tensions), 1)
        climax_positions = [
            i + 1 for i in range(1, len(tensions) - 1)
            if (tensions[i] > tensions[i-1] and tensions[i] > tensions[i+1]
                and tensions[i] > avg_tension * 1.4)
        ]

        hook_density = round(sum(hook_counts) / max(len(hook_counts), 1), 3)

        # Pacing segments
        def _pace(t: float) -> str:
            return "fast" if t > 0.6 else ("slow" if t < 0.3 else "medium")

        segments: list[dict] = []
        if tensions:
            cur = _pace(tensions[0])
            start = 0
            for i in range(1, len(tensions)):
                p = _pace(tensions[i])
                if p != cur:
                    segments.append({
                        "start_ch": start + 1,
                        "end_ch": i,
                        "pacing": cur,
                        "avg_tension": round(
                            sum(tensions[start:i]) / max(i - start, 1), 3),
                    })
                    cur = p
                    start = i
            segments.append({
                "start_ch": start + 1,
                "end_ch": len(tensions),
                "pacing": cur,
                "avg_tension": round(
                    sum(tensions[start:]) / max(len(tensions) - start, 1), 3),
            })

        return {
            "tension_curve": tensions,
            "avg_tension": round(avg_tension, 3),
            "climax_positions": climax_positions,
            "hook_density": hook_density,
            "opening_distribution": dict(openings.most_common()),
            "shuangdian": shuangdian_all,
            "pacing_segments": segments,
        }

    # ── LLM story arc analysis ────────────────────────────────

    async def _analyze_story_arc(
        self,
        title: str,
        total_chapters: int,
        chapter_results: list[dict],
    ) -> dict:
        """Use cloud LLM to analyze overall story arc from chapter summaries."""
        # Build summary text
        summary_lines = []
        for ch in chapter_results:
            ch_num = ch.get("chapter_num", 0)
            summary = ch.get("summary", {})
            text = summary.get("summary", "") if isinstance(summary, dict) else ""
            if text:
                summary_lines.append(f"第{ch_num}章：{text}")

        if not summary_lines:
            logger.warning("No chapter summaries available for story arc analysis")
            return {}

        summaries_text = "\n".join(summary_lines)

        # Truncate if too long (target ~80K chars for cloud model context)
        if len(summaries_text) > 80000:
            # Keep every Nth summary to fit
            step = len(summary_lines) // (80000 // 500)
            summary_lines = summary_lines[::max(step, 1)]
            summaries_text = "\n".join(summary_lines)

        prompt = _STORY_ARC_PROMPT.format(
            title=title,
            total_chapters=total_chapters,
            summaries=summaries_text,
        )

        try:
            raw = await self.model_router.invoke(
                role="novel_aggregator",
                prompt=prompt,
                max_tokens=8192,
                temperature=0.3,
            )
            return self._parse_json_obj(raw)
        except Exception:
            logger.exception("Story arc LLM analysis failed")
            return {}

    # ── Helpers ───────────────────────────────────────────────

    def _save_result(self, ref_id: str, result: dict) -> None:
        """Save aggregation result to extraction_progress."""
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO extraction_progress
                   (ref_id, phase, chapter_num, status, result_json, completed_at)
                   VALUES (?, 'novel_aggregate', 0, 'completed', ?, CURRENT_TIMESTAMP)
                   ON CONFLICT(ref_id, phase, chapter_num) DO UPDATE SET
                       status='completed',
                       result_json=excluded.result_json,
                       completed_at=CURRENT_TIMESTAMP""",
                (ref_id, json.dumps(result, ensure_ascii=False)),
            )

            # Also update reference_works with structured data
            conn.execute(
                """UPDATE reference_works SET
                       narrative_structure_json=?,
                       style_fingerprint_json=?,
                       preprocessing_status='done',
                       updated_at=CURRENT_TIMESTAMP
                   WHERE ref_id=?""",
                (
                    json.dumps(result.get("narrative_structure", {}), ensure_ascii=False),
                    json.dumps(result.get("technique_fingerprint", {}), ensure_ascii=False),
                    ref_id,
                ),
            )
            conn.commit()

    @staticmethod
    def _parse_json_obj(text: str) -> dict:
        import re
        for pat in [r"```json\s*([\s\S]*?)```", r"```\s*([\s\S]*?)```"]:
            m = re.search(pat, text)
            if m:
                try:
                    result = json.loads(m.group(1))
                    return result if isinstance(result, dict) else {}
                except json.JSONDecodeError:
                    continue
        try:
            result = json.loads(text)
            return result if isinstance(result, dict) else {}
        except json.JSONDecodeError:
            return {}
