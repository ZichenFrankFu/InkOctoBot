"""
Slop Detect skill — rule-based AI-flavor detection.

Uses pattern matching against known AI-generated text patterns
(arXiv:2509.19163). No LLM calls required.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from agents.base_skill import BaseSkill, SkillMeta

logger = logging.getLogger("inkoctobot.skills.slop_detect")

_PATTERNS_PATH = Path(__file__).resolve().parent.parent.parent.parent.parent / "config" / "slop_patterns.json"

# Built-in Chinese slop patterns
_DEFAULT_PATTERNS: list[dict[str, Any]] = [
    {"pattern": r"不禁", "weight": 0.3, "category": "cliche_phrase"},
    {"pattern": r"嘴角.*上扬", "weight": 0.5, "category": "cliche_action"},
    {"pattern": r"眼中闪过.*光芒", "weight": 0.5, "category": "cliche_action"},
    {"pattern": r"深吸一口气", "weight": 0.3, "category": "cliche_action"},
    {"pattern": r"心中一凛", "weight": 0.3, "category": "cliche_phrase"},
    {"pattern": r"宛如.*般", "weight": 0.2, "category": "cliche_simile"},
    {"pattern": r"如同.*一般", "weight": 0.2, "category": "cliche_simile"},
    {"pattern": r"紧紧握住.*拳头", "weight": 0.4, "category": "cliche_action"},
    {"pattern": r"值得注意的是", "weight": 0.6, "category": "ai_tell"},
    {"pattern": r"总而言之", "weight": 0.5, "category": "ai_tell"},
    {"pattern": r"综上所述", "weight": 0.7, "category": "ai_tell"},
    {"pattern": r"让我们", "weight": 0.4, "category": "ai_tell"},
]


class Skill(BaseSkill):
    """Rule-based AI slop detector — overrides execute() to skip LLM."""

    def __init__(self, patterns_path: str | Path | None = None) -> None:
        self.patterns = self._load_patterns(patterns_path)

    def meta(self) -> SkillMeta:
        return SkillMeta(
            name="slop_detect",
            display_name="AI味检测",
            description=(
                "基于规则的AI生成文本风格检测，使用模式匹配识别AI常见表达"
                "（参考arXiv:2509.19163），无需LLM调用。"
            ),
            version="1.0.0",
            model_role="none",
            tags=["evaluation", "quality", "slop", "ai-detection", "rule-based"],
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
                "required": ["score", "matches", "density", "has_issues"],
                "properties": {
                    "score": {"type": "number"},
                    "matches": {"type": "array"},
                    "density": {"type": "number"},
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
        """Run rule-based slop detection directly (no LLM)."""
        text: str = inputs.get("text", "")
        matches: list[dict[str, Any]] = []
        total_weight = 0.0

        for pat in self.patterns:
            regex = pat.get("pattern", "")
            found = re.findall(regex, text)
            if found:
                matches.append({
                    "pattern": regex,
                    "category": pat.get("category", "unknown"),
                    "count": len(found),
                    "weight": pat.get("weight", 0.5),
                    "examples": found[:3],
                })
                total_weight += pat.get("weight", 0.5) * len(found)

        # Normalize: 100 = no slop, 0 = heavy slop
        text_len = max(len(text), 1)
        density = total_weight / (text_len / 1000)
        score = max(0.0, 100.0 - density * 20)

        return {
            "score": round(score, 1),
            "matches": matches,
            "density": round(density, 3),
            "has_issues": score < 70,
        }

    # ── Internal helpers ────────────────────────────────────────

    @staticmethod
    def _load_patterns(path: str | Path | None) -> list[dict[str, Any]]:
        p = Path(path) if path else _PATTERNS_PATH
        if p.exists():
            try:
                with open(p, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                    if isinstance(loaded, list):
                        return loaded
                    return loaded.get("patterns", _DEFAULT_PATTERNS)
            except (json.JSONDecodeError, KeyError):
                pass
        return list(_DEFAULT_PATTERNS)
