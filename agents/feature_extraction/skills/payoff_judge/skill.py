"""爽点判断 — feature-extraction skill.

Detects 爽点 (reader-payoff beats) in webnovel text: face-slaps,
power reveals, breakthroughs, mystery reveals, etc.
"""
from __future__ import annotations

import json
import re
from typing import Any

from agents.base_skill import BaseSkill, SkillMeta

_PROMPT = """[自动化数据抽取 · 不是对话] 你的输出会被 `json.loads` 直接解析。

阅读下面的文本，识别其中的**爽点**——让读者产生爽快感 / 满足感的情节点
（打脸反转、实力展现、突破晋级、谜底揭开、扮猪吃虎、获得宝物等）。

**只输出**以 `{{` 开始、`}}` 结束的合法 JSON；无爽点时返回 `{{"payoffs":[]}}`：
{{
  "payoffs": [
    {{
      "type": "爽点类型：打脸反转 | 实力展现 | 突破晋级 | 谜底揭开 | 其他",
      "plot": "一句话写清谁对谁做了什么爽快的事，≤ 40 字"
    }}
  ]
}}

正文：
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
    """Detect reader-payoff beats in webnovel text."""

    def meta(self) -> SkillMeta:
        return SkillMeta(
            name="payoff_judge",
            display_name="爽点判断",
            description=(
                "识别文本中的「爽点」——打脸反转、实力展现、突破晋级、"
                "谜底揭开等令读者爽快的情节，输出类型与具体情节。"
            ),
            version="1.0.0",
            model_role="reference_extractor",
            max_tokens=2048,
            temperature=0.1,
            tags=["feature_extraction", "payoff", "shuangdian", "judgement"],
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
                f"payoff_judge: 输出不是合法 JSON ({e.msg})。"
                f"原始内容前 200 字：{raw[:200]!r}"
            ) from e
        if not isinstance(data, dict):
            raise ValueError("payoff_judge: 期望 JSON object")
        payoffs = data.get("payoffs")
        return {"payoffs": payoffs if isinstance(payoffs, list) else []}
