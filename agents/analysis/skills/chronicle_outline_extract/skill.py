"""Chronicle outline extraction skill — extracts a structured
epochs/periods/events outline from novel text.

This skill captures the **format contract** for the chronicle outline
(used by `PlotOutlineEditor` in the frontend) in one place, so the format
can be re-used:

  1. As a Skill the agent runtime can invoke.
  2. As a prompt the user can copy to a web LLM (ChatGPT / Claude.ai)
     when the configured model can't produce parseable output.

Note: the production extraction pipeline currently splits this into
three separate LLM calls (characters / settings / rhythm) for token
efficiency; this skill exposes the full one-shot prompt for users who
want to run the entire extraction in a single web-LLM turn.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from agents.base_skill import BaseSkill, SkillMeta

logger = logging.getLogger("inkoctobot.skills.chronicle_outline_extract")


_PROMPT_TEMPLATE = """[自动化数据抽取 · 不是对话] 你的输出会被 `json.loads` 直接解析；任何非 JSON 字符都会导致管线失败。

从下面的小说文本中提取**编年史**格式的剧情大纲。

**严格禁止**（违反则整条响应被视为错误）：
- 任何寒暄、解释、对话语句（如「你好」「这个故事讲的是」「让我告诉你」）
- ```json ... ``` 这样的 markdown 包装
- <think>...</think> 等推理块
- JSON 之外的任何文字

**只输出**：以 `{{` 开始、以 `}}` 结束的合法 JSON 对象。找不到任何事件时返回 `{{"logline":"","epochs":[]}}`。

JSON schema（字段名严格匹配）：

{{
  "logline": "≤ 50 字一句话概括，可空字符串",
  "epochs": [
    {{
      "title": "大段标题（通常是故事时间，如「1954 年」，或卷名）",
      "periods": [
        {{
          "title": "时间段标题（如「春」「主角入狱后」）",
          "time_marker": "可选；如「1954 年 3 月」「第 12 章」「约 5 万字处」",
          "events": [
            {{
              "subject": "事件主语（角色名 / 组织名 / 叙事者）",
              "category": "plot_main | plot_side | character | setting | conflict | revelation | foreshadow | other",
              "name": "事件名称（≤ 12 字）",
              "description": "1-2 句客观描述发生了什么，不剧透动机",
              "hidden": "可选；该事件背后尚未公开的真相或动机；无则空字符串",
              "time_marker": "可选；事件发生的时间锚点"
            }}
          ]
        }}
      ]
    }}
  ]
}}

时间锚点的填法（按优先级）：
  1. 原文有显式时间（「1954 年 3 月」「公元前 221 年」）→ 照写
  2. 否则填「第 N 章」
  3. 都不便确定 → 填「约 M 万字处」
  4. **不要编造日期**

本段元数据：
- 卷名 / 阶段标题：{segment_title}
- 章节数：{n_chapters}

文本：
{text}
"""


class Skill(BaseSkill):
    """Extract a chronicle-format outline (epochs/periods/events) from text."""

    def meta(self) -> SkillMeta:
        return SkillMeta(
            name="chronicle_outline_extract",
            display_name="编年史大纲提取",
            description=(
                "从小说文本中提取「编年史」格式的剧情大纲："
                "epochs（大段/纪元）→ periods（时间段）→ events（事件）。"
            ),
            version="1.0.0",
            model_role="reference_extractor",
            max_tokens=4096,
            temperature=0.1,
            tags=["analysis", "narrative", "outline", "chronicle", "extraction"],
            permissions=["read_narrative"],
            input_schema={
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "待分析的小说文本"},
                    "segment_title": {"type": "string", "description": "本段的卷名 / 阶段标题"},
                    "n_chapters": {"type": "integer", "description": "本段包含的章节数"},
                },
                "required": ["text"],
            },
            output_schema={
                "type": "object",
                "properties": {
                    "logline": {"type": "string"},
                    "epochs": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "title": {"type": "string"},
                                "periods": {"type": "array"},
                            },
                            "required": ["title", "periods"],
                        },
                    },
                },
                "required": ["logline", "epochs"],
            },
        )

    async def build_prompt(self, inputs: dict[str, Any]) -> str:
        text = inputs.get("text", "")
        segment_title = (inputs.get("segment_title") or "未指定").strip()
        n_chapters = int(inputs.get("n_chapters") or 0)
        return _PROMPT_TEMPLATE.format(
            segment_title=segment_title,
            n_chapters=n_chapters,
            text=text,
        )

    async def parse_output(self, raw: str) -> dict[str, Any]:
        """Strip reasoning / markdown fences and parse as JSON object.

        Mirrors `ai_extractor._strip_json` so this skill stays robust to
        chat-tuned models that emit `<think>` tags or ```json fences."""
        s = (raw or "").strip()
        # Strip <think>...</think> reasoning blocks and stray opening tags.
        s = re.sub(r"<think>.*?</think>", "", s, flags=re.DOTALL | re.IGNORECASE).strip()
        s = re.sub(r"<think>|</think>", "", s, flags=re.IGNORECASE).strip()
        # Strip a whole-response ```json fence.
        m = re.match(r"^```(?:json)?\s*(.*?)\s*```$", s, re.DOTALL)
        if m:
            s = m.group(1).strip()
        # Lock on first { and last }.
        a = s.find("{")
        b = s.rfind("}")
        if 0 <= a < b:
            s = s[a:b + 1]
        try:
            data = json.loads(s)
        except json.JSONDecodeError as e:
            raise ValueError(
                f"chronicle_outline_extract: 输出不是合法 JSON ({e.msg} at "
                f"line {e.lineno} col {e.colno})。原始内容前 200 字：{raw[:200]!r}"
            ) from e
        if not isinstance(data, dict):
            raise ValueError(
                f"chronicle_outline_extract: 期望 object，得到 {type(data).__name__}"
            )
        # Coerce minimal shape so downstream readers can rely on keys existing.
        data.setdefault("logline", "")
        data.setdefault("epochs", [])
        return data
