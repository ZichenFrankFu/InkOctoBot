"""
Chapter-level extraction — extracts writing techniques, chapter structure,
and generates summaries for each chapter.

Uses a hybrid approach:
  - Rule-based: rhetoric_classifier, narrative_extractor, style_extractor
  - LLM-based: advanced technique identification, structure analysis, summaries
"""
from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path
from typing import Any

from reference_pipeline.narrative_extractor import (
    _tension,
    _HOOK_PATS,
    _SHUANGDIAN,
    _OPENING,
)
from reference_pipeline.rhetoric_classifier import (
    classify_rhetoric,
    rhetoric_summary,
)

logger = logging.getLogger("inkoctobot.reference_ingest.skill_extraction.chapter_extractor")

# ── LLM prompt templates ─────────────────────────────────────────

_WRITING_TECHNIQUE_PROMPT = """\
你是一位专业的文学分析师。请分析以下小说章节中使用的写作手法。

重点识别以下类别的手法：
1. 视角与叙述技巧：视角切换、叙述距离变化、限制性视角、全知视角运用
2. 对话设计：对话节奏、潜台词、对话中的冲突推进、人物声音区分
3. 环境描写：以景写情、环境暗示、感官细节、氛围营造
4. 人物塑造：间接刻画、细节暗示、行为vs内心的张力、标志性动作/口头禅
5. 情感渲染：情绪递进、情感反差、共情锚点、煽情节制
6. 节奏控制：长短句交替、段落长度变化、场景切换速度

请以JSON格式输出，每个手法包含类型、名称、描述和原文示例：
```json
[
  {{
    "technique_type": "类别（如：视角与叙述、对话设计、环境描写等）",
    "technique_name": "具体手法名称",
    "description": "该手法如何运用的说明",
    "excerpt": "原文示例（不超过100字）"
  }}
]
```

只输出确实在文本中使用了的手法，不要臆造。

章节标题：{title}
章节内容：
{content}"""

_CHAPTER_STRUCTURE_PROMPT = """\
你是一位叙事结构分析专家。请分析以下小说章节的剧情结构设计。

分析以下方面：
1. 开头模式：如何吸引读者（悬念开头/对话开头/场景开头/承接上章等）
2. 冲突设置：本章的主要冲突是什么，如何引入
3. 高潮构建：如何一步步推向本章高潮
4. 结尾设计：如何收尾（钩子/悬念/情感余韵/转折等）
5. 场景转换：场景之间如何过渡
6. 节奏模式：张弛交替的整体安排

请以JSON格式输出：
```json
{{
  "opening_type": "开头类型",
  "opening_description": "开头手法描述",
  "conflict_setup": "冲突设置方式",
  "climax_build": "高潮构建方式",
  "ending_type": "结尾类型",
  "ending_hook": "结尾钩子描述（如有）",
  "scene_transitions": ["场景转换手法列表"],
  "pacing_pattern": "节奏模式描述"
}}
```

章节标题：{title}
章节内容：
{content}"""

_CHAPTER_SUMMARY_PROMPT = """\
请为以下小说章节生成结构化摘要，控制在200-500字。

摘要需包含：
1. 主要事件（按时间顺序）
2. 角色状态变化（哪些角色出现了什么变化）
3. 冲突进展（现有冲突如何发展，是否有新冲突）
4. 伏笔/悬念（本章埋下的伏笔或留下的悬念）

以JSON格式输出：
```json
{{
  "summary": "章节概要文字",
  "key_events": ["关键事件1", "关键事件2"],
  "character_changes": [{{"name": "角色名", "change": "变化描述"}}],
  "conflict_progress": "冲突进展描述",
  "foreshadowing": ["伏笔/悬念"]
}}
```

{prev_context}
章节标题：{title}
章节内容：
{content}"""


class ChapterExtractor:
    """Extract writing techniques, chapter structure, and summaries per chapter."""

    def __init__(self, db_path: str | Path, model_router: Any = None):
        self.db_path = str(db_path)
        self.model_router = model_router

    def _conn(self) -> sqlite3.Connection:
        c = sqlite3.connect(self.db_path)
        c.execute("PRAGMA journal_mode=WAL")
        c.row_factory = sqlite3.Row
        return c

    # ── Public API ────────────────────────────────────────────

    async def extract_novel(
        self,
        ref_id: str,
        *,
        resume: bool = True,
        max_content_for_llm: int = 8000,
    ) -> dict[str, Any]:
        """
        Run chapter-level extraction for all chapters of a novel.

        Args:
            ref_id: Reference ID of the novel.
            resume: Skip already-completed chapters.
            max_content_for_llm: Max chars to send to LLM per chapter.
        """
        # Load chapter list
        with self._conn() as conn:
            chapters = conn.execute(
                "SELECT * FROM novel_chapters WHERE ref_id=? AND is_author_note=0 "
                "ORDER BY chapter_num",
                (ref_id,),
            ).fetchall()

            if resume:
                completed = {
                    row["chapter_num"]
                    for row in conn.execute(
                        "SELECT chapter_num FROM extraction_progress "
                        "WHERE ref_id=? AND phase='chapter_extract' AND status='completed'",
                        (ref_id,),
                    ).fetchall()
                }
            else:
                completed = set()

        total = len(chapters)
        skipped = 0
        processed = 0
        prev_summary = ""

        for ch_row in chapters:
            ch_num = ch_row["chapter_num"]

            if ch_num in completed:
                # Load previous summary for context continuity
                with self._conn() as conn:
                    prev = conn.execute(
                        "SELECT result_json FROM extraction_progress "
                        "WHERE ref_id=? AND phase='chapter_extract' AND chapter_num=?",
                        (ref_id, ch_num),
                    ).fetchone()
                    if prev and prev["result_json"]:
                        try:
                            data = json.loads(prev["result_json"])
                            prev_summary = data.get("summary", {}).get("summary", "")
                        except (json.JSONDecodeError, AttributeError):
                            pass
                skipped += 1
                continue

            # Mark in-progress
            self._update_progress(ref_id, ch_num, "in_progress")

            try:
                # Read chapter content
                content = Path(ch_row["file_path"]).read_text(encoding="utf-8")
                # Strip the title line (first line)
                lines = content.split("\n", 2)
                title = lines[0].strip() if lines else ""
                body = lines[2].strip() if len(lines) > 2 else content

                # Run extraction
                result = await self.extract_chapter(
                    chapter_num=ch_num,
                    title=title,
                    content=body,
                    prev_summary=prev_summary,
                    max_content_for_llm=max_content_for_llm,
                )

                # Save result
                self._update_progress(
                    ref_id, ch_num, "completed",
                    result_json=json.dumps(result, ensure_ascii=False),
                )

                # Update prev_summary for next chapter
                summary_data = result.get("summary", {})
                prev_summary = summary_data.get("summary", "")

                processed += 1
                if processed % 50 == 0:
                    logger.info("Progress: %d/%d chapters", processed + skipped, total)

            except Exception as e:
                logger.exception("Failed chapter %d of %s", ch_num, ref_id)
                self._update_progress(ref_id, ch_num, "failed", error=str(e))

        logger.info(
            "Chapter extraction complete for %s: %d processed, %d skipped, %d total",
            ref_id, processed, skipped, total,
        )
        return {"processed": processed, "skipped": skipped, "total": total}

    async def extract_chapter(
        self,
        chapter_num: int,
        title: str,
        content: str,
        prev_summary: str = "",
        max_content_for_llm: int = 8000,
    ) -> dict[str, Any]:
        """
        Extract all three categories from a single chapter.

        Returns dict with keys: writing_techniques, chapter_structure, summary
        """
        result: dict[str, Any] = {}

        # 1. Rule-based extraction (always runs)
        result["writing_techniques_rule"] = self._extract_techniques_rule(content)
        result["chapter_structure_rule"] = self._extract_structure_rule(title, content)

        # 2. LLM-based extraction (if model_router available)
        if self.model_router:
            truncated = content[:max_content_for_llm]

            result["writing_techniques_llm"] = await self._extract_techniques_llm(
                title, truncated,
            )
            result["chapter_structure_llm"] = await self._extract_structure_llm(
                title, truncated,
            )
            result["summary"] = await self._extract_summary(
                title, truncated, prev_summary,
            )
        else:
            result["writing_techniques_llm"] = []
            result["chapter_structure_llm"] = {}
            result["summary"] = {"summary": "", "key_events": [], "character_changes": [],
                                 "conflict_progress": "", "foreshadowing": []}

        return result

    # ── Rule-based extraction ─────────────────────────────────

    def _extract_techniques_rule(self, content: str) -> dict[str, Any]:
        """Rule-based writing technique extraction."""
        rhetoric = rhetoric_summary(content)
        rhetoric_details = classify_rhetoric(content)

        # Keep top examples per type
        examples_by_type: dict[str, list] = {}
        for item in rhetoric_details:
            rt = item["rhetoric_type"]
            if rt not in examples_by_type:
                examples_by_type[rt] = []
            if len(examples_by_type[rt]) < 3:
                examples_by_type[rt].append({
                    "sentence": item["sentence"][:200],
                    "confidence": item["confidence"],
                })

        return {
            "rhetoric": rhetoric,
            "rhetoric_examples": examples_by_type,
        }

    def _extract_structure_rule(self, title: str, content: str) -> dict[str, Any]:
        """Rule-based chapter structure extraction."""
        tension = _tension(content)

        # Opening pattern
        first_500 = content[:500]
        opening = "other"
        for name, pat in _OPENING.items():
            if pat.search(first_500):
                opening = name
                break

        # Hooks at chapter end
        tail = content[-300:]
        hook_count = sum(1 for p in _HOOK_PATS if p.search(tail))

        # Shuangdian
        shuangdian_types = []
        for cat, pats in _SHUANGDIAN.items():
            if any(p.search(content) for p in pats):
                shuangdian_types.append(cat)

        return {
            "tension": tension,
            "opening_pattern": opening,
            "hook_count": hook_count,
            "shuangdian_types": shuangdian_types,
        }

    # ── LLM-based extraction ─────────────────────────────────

    async def _extract_techniques_llm(
        self, title: str, content: str,
    ) -> list[dict]:
        """LLM-based writing technique extraction."""
        prompt = _WRITING_TECHNIQUE_PROMPT.format(title=title, content=content)
        try:
            raw = await self.model_router.invoke(
                role="chapter_extractor",
                prompt=prompt,
                max_tokens=4096,
                temperature=0.3,
            )
            return self._parse_json_list(raw)
        except Exception:
            logger.exception("LLM writing technique extraction failed")
            return []

    async def _extract_structure_llm(
        self, title: str, content: str,
    ) -> dict:
        """LLM-based chapter structure analysis."""
        prompt = _CHAPTER_STRUCTURE_PROMPT.format(title=title, content=content)
        try:
            raw = await self.model_router.invoke(
                role="chapter_extractor",
                prompt=prompt,
                max_tokens=2048,
                temperature=0.3,
            )
            return self._parse_json_obj(raw)
        except Exception:
            logger.exception("LLM chapter structure extraction failed")
            return {}

    async def _extract_summary(
        self, title: str, content: str, prev_summary: str,
    ) -> dict:
        """LLM-based chapter summary generation."""
        prev_ctx = (
            f"上一章摘要：{prev_summary}\n\n" if prev_summary
            else "（这是第一章或没有上一章摘要）\n\n"
        )
        prompt = _CHAPTER_SUMMARY_PROMPT.format(
            title=title, content=content, prev_context=prev_ctx,
        )
        try:
            raw = await self.model_router.invoke(
                role="chapter_extractor",
                prompt=prompt,
                max_tokens=2048,
                temperature=0.3,
            )
            return self._parse_json_obj(raw)
        except Exception:
            logger.exception("LLM summary generation failed")
            return {"summary": "", "key_events": [], "character_changes": [],
                    "conflict_progress": "", "foreshadowing": []}

    # ── Helpers ───────────────────────────────────────────────

    def _update_progress(
        self, ref_id: str, chapter_num: int, status: str,
        result_json: str | None = None, error: str | None = None,
    ) -> None:
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO extraction_progress
                   (ref_id, phase, chapter_num, status, result_json, error_message,
                    started_at, completed_at)
                   VALUES (?, 'chapter_extract', ?, ?, ?, ?,
                           CASE WHEN ?='in_progress' THEN CURRENT_TIMESTAMP END,
                           CASE WHEN ?='completed' THEN CURRENT_TIMESTAMP END)
                   ON CONFLICT(ref_id, phase, chapter_num) DO UPDATE SET
                       status=excluded.status,
                       result_json=COALESCE(excluded.result_json, extraction_progress.result_json),
                       error_message=excluded.error_message,
                       started_at=CASE WHEN excluded.status='in_progress'
                                       THEN CURRENT_TIMESTAMP
                                       ELSE extraction_progress.started_at END,
                       completed_at=CASE WHEN excluded.status IN ('completed','failed')
                                        THEN CURRENT_TIMESTAMP
                                        ELSE extraction_progress.completed_at END
                """,
                (ref_id, chapter_num, status, result_json, error,
                 status, status),
            )
            conn.commit()

    @staticmethod
    def _parse_json_list(text: str) -> list:
        """Extract a JSON array from LLM output."""
        import re as _re
        for pat in [r"```json\s*([\s\S]*?)```", r"```\s*([\s\S]*?)```"]:
            m = _re.search(pat, text)
            if m:
                try:
                    result = json.loads(m.group(1))
                    return result if isinstance(result, list) else []
                except json.JSONDecodeError:
                    continue
        try:
            result = json.loads(text)
            return result if isinstance(result, list) else []
        except json.JSONDecodeError:
            return []

    @staticmethod
    def _parse_json_obj(text: str) -> dict:
        """Extract a JSON object from LLM output."""
        import re as _re
        for pat in [r"```json\s*([\s\S]*?)```", r"```\s*([\s\S]*?)```"]:
            m = _re.search(pat, text)
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
