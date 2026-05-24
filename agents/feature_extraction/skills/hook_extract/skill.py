"""钩子识别 — feature-extraction skill.

Detects hooks (suspense / tension points) in text and tags each with a
position: 章首 / 段中 / 章末.
"""
from __future__ import annotations

import json
import re
from typing import Any

from agents.base_skill import BaseSkill, SkillMeta

_HOOK_POS = {"章首", "段中", "章末"}

_PROMPT = """[自动化数据抽取 · 不是对话] 你的输出会被 `json.loads` 直接解析。

阅读下面的文本，识别其中的**钩子**——悬念、未解之谜、张力点等引导读者
继续往下读的设计。

**只输出**以 `{{` 开始、`}}` 结束的合法 JSON；无钩子时返回 `{{"hooks":[]}}`：
{{
  "hooks": [
    {{
      "position": "钩子位置：章首 | 段中 | 章末",
      "content": "钩子内容，≤ 25 字"
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
    """Detect hooks (suspense points) in text."""

    def meta(self) -> SkillMeta:
        return SkillMeta(
            name="hook_extract",
            display_name="钩子识别",
            description=(
                "识别文本中的「钩子」——章首悬念、段中张力点、章末未解之谜"
                "等引导读者继续阅读的张力设计。"
            ),
            version="1.0.0",
            model_role="reference_extractor",
            max_tokens=1024,
            temperature=0.1,
            tags=["feature_extraction", "hook", "suspense", "extraction"],
            input_schema={
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            },
            output_schema={
                "type": "object",
                "properties": {
                    "hooks": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "position": {"type": "string"},
                                "content": {"type": "string"},
                            },
                        },
                    },
                },
                "required": ["hooks"],
            },
        )

    async def build_prompt(self, inputs: dict[str, Any]) -> str:
        return _PROMPT.format(text=inputs.get("text", ""))

    async def parse_output(self, raw: str) -> dict[str, Any]:
        try:
            data = _parse_json_obj(raw)
        except json.JSONDecodeError as e:
            raise ValueError(
                f"hook_extract: 输出不是合法 JSON ({e.msg})。"
                f"原始内容前 200 字：{raw[:200]!r}"
            ) from e
        if not isinstance(data, dict):
            raise ValueError("hook_extract: 期望 JSON object")
        hooks: list[dict] = []
        for h in (data.get("hooks") or []):
            if not isinstance(h, dict):
                continue
            pos = str(h.get("position") or "章末").strip()
            hooks.append({
                "position": pos if pos in _HOOK_POS else "章末",
                "content": str(h.get("content") or "").strip(),
            })
        return {"hooks": hooks}
