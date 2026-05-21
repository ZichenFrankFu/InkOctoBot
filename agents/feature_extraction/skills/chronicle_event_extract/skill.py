"""编年史事件写作 — feature-extraction skill.

Extracts time-ordered chronicle events from novel text. Mirrors the
event half of the reference-works `reference.unified` extraction so it
can also be run standalone or via a web LLM.
"""
from __future__ import annotations

import json
import re
from typing import Any

from agents.base_skill import BaseSkill, SkillMeta

_PROMPT = """[自动化数据抽取 · 不是对话] 你的输出会被 `json.loads` 直接解析；任何非 JSON 字符都会导致管线失败。

阅读下面的小说文本，按故事时间顺序抽取**编年史事件**。

**严格禁止**：寒暄 / 解释 / markdown 包装 / <think> 推理块 / JSON 之外的任何文字。
**只输出**：以 `{{` 开始、以 `}}` 结束的合法 JSON 对象；无事件时返回 `{{"events":[]}}`。

JSON schema（字段名严格匹配）：
{{
  "events": [
    {{
      "subject": "事件主语：角色名 / 组织名 / 「叙事者」",
      "category": "plot_main | plot_side | character | setting | conflict | revelation | foreshadow | other",
      "name": "事件名，≤ 12 字，章节弧标题级别",
      "description": "1 句客观陈述本事件，≤ 60 字，不写动机或心理",
      "time_marker": "时间锚点；优先用书中显式时间，其次「第 N 章」"
    }}
  ]
}}

要求：颗粒度为章节弧级别，每章 1-3 条最佳；不要写镜头级细节；不要编造日期。

本段共约 {n_chapters} 章。正文：
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
    """Extract chronicle events from novel text."""

    def meta(self) -> SkillMeta:
        return SkillMeta(
            name="chronicle_event_extract",
            display_name="编年史事件写作",
            description=(
                "从小说文本中按时间顺序抽取「编年史」事件条目"
                "（主语 / 类别 / 事件名 / 描述 / 时间锚点）。"
            ),
            version="1.0.0",
            model_role="reference_extractor",
            max_tokens=4096,
            temperature=0.1,
            tags=["feature_extraction", "chronicle", "event", "extraction"],
            input_schema={
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "n_chapters": {"type": "integer"},
                },
                "required": ["text"],
            },
        )

    async def build_prompt(self, inputs: dict[str, Any]) -> str:
        return _PROMPT.format(
            text=inputs.get("text", ""),
            n_chapters=int(inputs.get("n_chapters") or 0),
        )

    async def parse_output(self, raw: str) -> dict[str, Any]:
        try:
            data = _parse_json_obj(raw)
        except json.JSONDecodeError as e:
            raise ValueError(
                f"chronicle_event_extract: 输出不是合法 JSON ({e.msg})。"
                f"原始内容前 200 字：{raw[:200]!r}"
            ) from e
        if not isinstance(data, dict):
            raise ValueError("chronicle_event_extract: 期望 JSON object")
        events = data.get("events")
        return {"events": events if isinstance(events, list) else []}
