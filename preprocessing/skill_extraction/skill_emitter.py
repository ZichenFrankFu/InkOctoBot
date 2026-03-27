"""
Skill emitter — generates learned_skills and stores patterns into ChromaDB.

Two output forms:
  A. Generates SKILL.md + skill.py in agents/learned_skills/
  B. Stores pattern descriptions + examples into ChromaDB vector store
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
import textwrap
from pathlib import Path
from typing import Any

logger = logging.getLogger("inkoctobot.preprocessing.skill_extraction.skill_emitter")


# ── Skill code templates ─────────────────────────────────────────

_SKILL_MD_TEMPLATE = """\
---
name: {name}
display_name: {display_name}
description: {description}
version: 1.0.0
model_role: default
tags: [{tags}]
permissions: [read_reference_db]
---

## Description

{long_description}

## Input

| Field | Type | Required | Description |
| ----- | ---- | -------- | ----------- |
| context | string | Yes | Current writing context (scene/chapter) |
| request | string | No | Specific aspect to advise on |

## Output

Returns JSON with technique guidance:
```json
{{
  "techniques": [
    {{
      "name": "technique name",
      "description": "how to apply",
      "example": "reference example"
    }}
  ],
  "advice": "overall writing advice for this context"
}}
```
"""

_SKILL_PY_TEMPLATE = '''\
"""Skill: {name} — {display_name}

Auto-generated from novel corpus extraction pipeline.
Based on analysis of {novel_count} novels.
"""
from agents.base_skill import BaseSkill, SkillMeta


class Skill(BaseSkill):
    """Provide writing guidance based on patterns from {novel_count} novels."""

    # Pattern knowledge extracted from corpus
    PATTERNS = {patterns_json}

    def meta(self) -> SkillMeta:
        return SkillMeta(
            name="{name}",
            display_name="{display_name}",
            description="{description}",
            tags=[{tags_py}],
            permissions=["read_reference_db"],
        )

    async def build_prompt(self, inputs: dict) -> str:
        context = inputs.get("context", "")
        request = inputs.get("request", "")

        patterns_text = "\\n".join(
            f"- {{p['pattern_name']}}：{{p['description']}}"
            for p in self.PATTERNS
        )

        return (
            f"你是一位网文写作顾问。以下是从大量优秀网文中提取的{{self.meta().display_name}}模式库：\\n\\n"
            f"{{patterns_text}}\\n\\n"
            f"当前写作场景：\\n{{context}}\\n\\n"
            f"{{'具体需求：' + request + chr(10) if request else ''}}"
            f"请基于以上模式库，为当前场景推荐最适合的技巧，并给出具体的运用建议。\\n"
            f"以JSON格式输出：\\n"
            f'{{"techniques": [{{"name": "...", "description": "...", "example": "..."}}], '
            f'"advice": "..."}}'
        )

    async def parse_output(self, raw: str) -> dict:
        result = self.extract_json(raw)
        if result and isinstance(result, dict):
            return result
        return {{"techniques": [], "advice": raw[:500]}}
'''


class SkillEmitter:
    """Generate learned skills and populate vector store from mined patterns."""

    def __init__(
        self,
        db_path: str | Path,
        learned_skills_dir: str | Path | None = None,
        vector_store: Any = None,
    ):
        self.db_path = str(db_path)
        self.learned_skills_dir = Path(
            learned_skills_dir
            or Path(__file__).resolve().parent.parent.parent / "agents" / "learned_skills"
        )
        self.vector_store = vector_store

    def _conn(self) -> sqlite3.Connection:
        c = sqlite3.connect(self.db_path)
        c.execute("PRAGMA journal_mode=WAL")
        c.row_factory = sqlite3.Row
        return c

    def emit_all(self) -> dict[str, int]:
        """Emit skills for all categories."""
        counts = {}
        for category in ("writing_technique", "chapter_design", "story_arc"):
            count = self.emit_category(category)
            counts[category] = count
        return counts

    def emit_category(self, category: str) -> int:
        """Emit skills for a single category."""
        patterns = self._load_patterns(category)
        if not patterns:
            logger.warning("No patterns to emit for %s", category)
            return 0

        # Group patterns by subcategory for skill grouping
        groups: dict[str, list[dict]] = {}
        for pat in patterns:
            sub = pat.get("subcategory", "general")
            if sub not in groups:
                groups[sub] = []
            groups[sub].append(pat)

        count = 0
        for subcategory, group_patterns in groups.items():
            if len(group_patterns) < 1:
                continue

            # Generate skill
            skill_name = self._make_skill_name(category, subcategory)
            self._emit_skill_files(category, subcategory, skill_name, group_patterns)
            self._emit_to_vector_store(category, subcategory, group_patterns)
            self._mark_emitted(group_patterns)
            count += 1

        logger.info("Emitted %d skills for %s", count, category)
        return count

    # ── Skill file generation ─────────────────────────────────

    def _emit_skill_files(
        self,
        category: str,
        subcategory: str,
        skill_name: str,
        patterns: list[dict],
    ) -> None:
        """Generate SKILL.md + skill.py files."""
        skill_dir = self.learned_skills_dir / skill_name
        skill_dir.mkdir(parents=True, exist_ok=True)

        prefix_map = {
            "writing_technique": "wt",
            "chapter_design": "cd",
            "story_arc": "sa",
        }
        prefix = prefix_map.get(category, "sk")

        display_name_map = {
            "writing_technique": "写作手法",
            "chapter_design": "章节设计",
            "story_arc": "剧情设计",
        }
        category_cn = display_name_map.get(category, category)
        display_name = f"{category_cn}:{subcategory}"

        description = (
            f"基于小说语料库提取的{category_cn}模式 — {subcategory}。"
            f"包含{len(patterns)}个识别出的模式。"
        )

        long_description = description + "\n\n### 包含的模式：\n"
        for pat in patterns[:10]:
            long_description += f"\n- **{pat.get('pattern_name', '')}**: {pat.get('description', '')[:100]}"

        tags = f"learned, {category}, {subcategory}"
        tags_py = ", ".join(f'"{t.strip()}"' for t in tags.split(","))

        # Count novel sources
        novel_count = max(
            pat.get("frequency", 1) for pat in patterns
        ) if patterns else 0

        # Prepare patterns data for embedding in code
        patterns_data = [
            {
                "pattern_name": p.get("pattern_name", ""),
                "description": p.get("description", "")[:300],
                "when_to_use": p.get("when_to_use", ""),
                "key_points": p.get("key_points", []),
            }
            for p in patterns[:20]
        ]

        # Write SKILL.md
        (skill_dir / "SKILL.md").write_text(
            _SKILL_MD_TEMPLATE.format(
                name=skill_name,
                display_name=display_name,
                description=description,
                long_description=long_description,
                tags=tags,
            ),
            encoding="utf-8",
        )

        # Write skill.py
        (skill_dir / "skill.py").write_text(
            _SKILL_PY_TEMPLATE.format(
                name=skill_name,
                display_name=display_name,
                description=description,
                novel_count=novel_count,
                patterns_json=json.dumps(patterns_data, ensure_ascii=False, indent=4),
                tags_py=tags_py,
            ),
            encoding="utf-8",
        )

        logger.info("Emitted skill: %s (%d patterns)", skill_name, len(patterns))

    # ── Vector store emission ─────────────────────────────────

    def _emit_to_vector_store(
        self,
        category: str,
        subcategory: str,
        patterns: list[dict],
    ) -> None:
        """Store patterns in ChromaDB for RAG retrieval."""
        if not self.vector_store:
            return

        ids = []
        texts = []
        metadatas = []

        for i, pat in enumerate(patterns):
            doc_id = f"pattern_{category}_{subcategory}_{i}"
            text = (
                f"{pat.get('pattern_name', '')}: {pat.get('description', '')}\n"
                f"适用场景: {pat.get('when_to_use', '')}\n"
                f"要点: {', '.join(pat.get('key_points', []))}"
            )
            metadata = {
                "type": "extracted_pattern",
                "category": category,
                "subcategory": subcategory,
                "pattern_name": pat.get("pattern_name", ""),
                "quality_score": pat.get("quality_score", 0),
            }

            ids.append(doc_id)
            texts.append(text)
            metadatas.append(metadata)

        if ids:
            self.vector_store.add_batch(ids, texts, metadatas)
            logger.info(
                "Added %d patterns to vector store: %s/%s",
                len(ids), category, subcategory,
            )

    # ── Helpers ───────────────────────────────────────────────

    def _load_patterns(self, category: str) -> list[dict]:
        """Load patterns from extracted_patterns table."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM extracted_patterns WHERE category=? "
                "ORDER BY quality_score DESC",
                (category,),
            ).fetchall()
        return [dict(r) for r in rows]

    def _make_skill_name(self, category: str, subcategory: str) -> str:
        """Generate a valid skill name."""
        prefix_map = {
            "writing_technique": "wt",
            "chapter_design": "cd",
            "story_arc": "sa",
        }
        prefix = prefix_map.get(category, "sk")
        safe_sub = re.sub(r"[^a-zA-Z0-9_\u4e00-\u9fff]", "_", subcategory)
        safe_sub = re.sub(r"_+", "_", safe_sub).strip("_")
        return f"{prefix}_{safe_sub}"

    def _mark_emitted(self, patterns: list[dict]) -> None:
        """Mark patterns as emitted in DB."""
        with self._conn() as conn:
            for pat in patterns:
                pid = pat.get("pattern_id")
                if pid:
                    conn.execute(
                        "UPDATE extracted_patterns SET skill_emitted=1 WHERE pattern_id=?",
                        (pid,),
                    )
            conn.commit()
