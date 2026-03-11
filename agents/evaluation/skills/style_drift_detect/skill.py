"""
Style Drift Detect skill — rule-based statistical style comparison.

Compares text features (sentence length, dialogue ratio, etc.) against
a target style profile to detect drift. No LLM calls required.
"""
from __future__ import annotations

import logging
import re
from typing import Any

from agents.base_skill import BaseSkill, SkillMeta

logger = logging.getLogger("inkoctobot.skills.style_drift_detect")

_DEVIATION_THRESHOLD = 0.3  # 30% deviation triggers a drift flag


class Skill(BaseSkill):
    """Rule-based style drift detector — overrides execute() to skip LLM."""

    def meta(self) -> SkillMeta:
        return SkillMeta(
            name="style_drift_detect",
            display_name="风格漂移检测",
            description=(
                "基于规则的统计风格偏移检测，将文本特征与目标风格画像进行比较，"
                "无需LLM调用。"
            ),
            version="1.0.0",
            model_role="none",
            tags=["evaluation", "quality", "style", "drift", "rule-based"],
            permissions=[],
            input_schema={
                "type": "object",
                "required": ["text"],
                "properties": {
                    "text": {"type": "string"},
                    "target_profile": {
                        "type": "object",
                        "additionalProperties": {"type": "number"},
                    },
                },
            },
            output_schema={
                "type": "object",
                "required": ["drifted", "drifts", "features", "score"],
                "properties": {
                    "drifted": {"type": "boolean"},
                    "drifts": {"type": "array"},
                    "features": {"type": "object"},
                    "score": {"type": "number"},
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
        """Run rule-based style drift detection directly (no LLM)."""
        text: str = inputs.get("text", "")
        target_profile: dict[str, float] = inputs.get("target_profile", {})

        current = self._extract_features(text)

        if not target_profile:
            return {
                "drifted": False,
                "drifts": [],
                "features": current,
                "score": 100,
            }

        drifts: list[dict[str, Any]] = []
        for key, target_val in target_profile.items():
            current_val = current.get(key, 0.0)
            if target_val > 0:
                deviation = abs(current_val - target_val) / target_val
                if deviation > _DEVIATION_THRESHOLD:
                    drifts.append({
                        "feature": key,
                        "target": round(target_val, 3),
                        "current": round(current_val, 3),
                        "deviation": round(deviation, 3),
                    })

        score = max(0, 100 - len(drifts) * 20)
        return {
            "drifted": len(drifts) > 0,
            "drifts": drifts,
            "features": current,
            "score": score,
        }

    # ── Internal helpers ────────────────────────────────────────

    @staticmethod
    def _extract_features(text: str) -> dict[str, float]:
        sentences = re.split(r"[。！？]+", text)
        sentences = [s.strip() for s in sentences if s.strip()]
        avg_sent_len = sum(len(s) for s in sentences) / max(len(sentences), 1)

        dialogues = re.findall(r'[""「」]', text)
        dialogue_ratio = len(dialogues) / max(len(text), 1) * 50

        paragraphs = [p for p in text.split("\n") if p.strip()]
        avg_para_len = sum(len(p) for p in paragraphs) / max(len(paragraphs), 1)

        return {
            "avg_sentence_length": round(avg_sent_len, 2),
            "dialogue_ratio": round(dialogue_ratio, 4),
            "avg_paragraph_length": round(avg_para_len, 2),
            "total_length": float(len(text)),
        }
