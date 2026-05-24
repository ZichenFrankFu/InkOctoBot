"""开篇模式判断 — feature-extraction skill.

Classifies a work's opening into one of four narrative patterns.
"""
from __future__ import annotations

import json
import re
from typing import Any

from agents.base_skill import BaseSkill, SkillMeta

_PATTERNS = {"in_medias_res", "dialogue_open", "worldbuilding", "character_intro"}

_PROMPT = """[自动化数据抽取 · 不是对话] 你的输出会被 `json.loads` 直接解析。

阅读下面的作品开篇文本，判断它的**开篇方式**，从以下四类中选 1 个：
- in_medias_res 高潮开局：直接切入冲突或关键场面，事后再补叙背景
- dialogue_open 对话开局：以人物对话开场，靠台词带出信息与张力
- worldbuilding 世界观铺陈：先铺陈世界观与背景设定，再引入主线人物
- character_intro 人物登场：以主要人物的登场和日常切入故事

**只输出**以 `{{` 开始、`}}` 结束的合法 JSON：
{{
  "opening_pattern": "上述四个英文 key 之一",
  "reason": "≤ 40 字判断依据"
}}

开篇正文：
{text}
"""


def _parse_json_obj(raw: str) -> Any:
    s = (raw or "").strip()
    s = re.sub(r"<think>.*?</think>", "", s, flags=re.DOTALL | re.IGNORECASE).strip()
    m = re.match(r"^```(?:json)?\s*(.*?)\s*```$", s, re.DOTALL)
    if m:
        s = m.group(1).strip()
    a, b = s.find("{"), s.rfind("}")
    if 0 <= a < b:
        s = s[a:b + 1]
    return json.loads(s)


class Skill(BaseSkill):
    """Classify a work's opening narrative pattern."""

    def meta(self) -> SkillMeta:
        return SkillMeta(
            name="opening_pattern_judge",
            display_name="开篇模式判断",
            description=(
                "判断作品开篇所采用的叙事手法——高潮开局 / 对话开局 / "
                "世界观铺陈 / 人物登场，输出英文 key 与判断理由。"
            ),
            version="1.0.0",
            model_role="reference_extractor",
            max_tokens=512,
            temperature=0.1,
            tags=["feature_extraction", "opening", "narrative", "judgement"],
            input_schema={
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            },
            output_schema={
                "type": "object",
                "properties": {
                    "opening_pattern": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["opening_pattern", "reason"],
            },
        )

    async def build_prompt(self, inputs: dict[str, Any]) -> str:
        return _PROMPT.format(text=inputs.get("text", ""))

    async def parse_output(self, raw: str) -> dict[str, Any]:
        try:
            data = _parse_json_obj(raw)
        except json.JSONDecodeError as e:
            raise ValueError(
                f"opening_pattern_judge: 输出不是合法 JSON ({e.msg})。"
                f"原始内容前 200 字：{raw[:200]!r}"
            ) from e
        if not isinstance(data, dict):
            raise ValueError("opening_pattern_judge: 期望 JSON object")
        pattern = str(data.get("opening_pattern") or "").strip()
        return {
            "opening_pattern": pattern if pattern in _PATTERNS else "",
            "reason": str(data.get("reason") or ""),
        }
