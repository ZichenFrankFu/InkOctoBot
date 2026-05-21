"""信息密度判断 — feature-extraction skill.

Judges the information density (0–1) of a chapter: how much plot,
worldbuilding and setup it actually carries. Padding lowers the score.
"""
from __future__ import annotations

import json
import re
from typing import Any

from agents.base_skill import BaseSkill, SkillMeta

_PROMPT = """[自动化数据抽取 · 不是对话] 你的输出会被 `json.loads` 直接解析。

阅读下面的章节文本，判断它的**信息密度**。
信息密度 = 单位篇幅里推进剧情 / 抛出设定 / 做铺垫的有效信息量；灌水、重复、
拖沓越多分值越低。参考刻度：过渡或灌水章 0.2-0.4，普通章 0.5-0.6，
信息量大的关键章 0.8-1.0。

**只输出**以 `{{` 开始、`}}` 结束的合法 JSON：
{{
  "info_density": 0–1 之间的浮点数,
  "reason": "≤ 40 字说明评分依据"
}}

章节正文：
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
    """Judge the information density of a chapter."""

    def meta(self) -> SkillMeta:
        return SkillMeta(
            name="info_density_judge",
            display_name="信息密度判断",
            description=(
                "判断一章/一段文本的信息密度（推进剧情、抛设定、铺垫的"
                "有效信息量），输出 0–1 浮点分值并说明理由。"
            ),
            version="1.0.0",
            model_role="reference_extractor",
            max_tokens=512,
            temperature=0.1,
            tags=["feature_extraction", "info_density", "pacing", "judgement"],
            input_schema={
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            },
        )

    async def build_prompt(self, inputs: dict[str, Any]) -> str:
        return _PROMPT.format(text=inputs.get("text", ""))

    async def parse_output(self, raw: str) -> dict[str, Any]:
        try:
            data = _parse_json_obj(raw)
        except json.JSONDecodeError as e:
            raise ValueError(
                f"info_density_judge: 输出不是合法 JSON ({e.msg})。"
                f"原始内容前 200 字：{raw[:200]!r}"
            ) from e
        if not isinstance(data, dict):
            raise ValueError("info_density_judge: 期望 JSON object")
        try:
            density = float(data.get("info_density"))
        except (TypeError, ValueError):
            density = 0.0
        density = max(0.0, min(1.0, density))
        return {"info_density": round(density, 3), "reason": str(data.get("reason") or "")}
