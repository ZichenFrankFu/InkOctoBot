---
name: slop_detect
display_name: AI味检测
description: 基于规则的AI生成文本风格检测，使用模式匹配识别AI常见表达（参考arXiv:2509.19163），无需LLM调用。
version: 1.0.0
model_role: none
tags: [evaluation, quality, slop, ai-detection, rule-based]
permissions: []
---

## Description

Rule-based AI-flavor (slop) detection using pattern matching against a
configurable library of known AI-generated text patterns. Based on slop
detection research (arXiv:2509.19163). Identifies cliche phrases, cliche
actions, cliche similes, and AI-tell markers. Operates without LLM calls.

## Input

| Field | Type   | Required | Description             |
|-------|--------|----------|-------------------------|
| text  | string | yes      | The text to analyze     |

## Output

```json
{
  "score": 75.0,
  "matches": [
    {
      "pattern": "不禁",
      "category": "cliche_phrase",
      "count": 3,
      "weight": 0.3,
      "examples": ["不禁", "不禁", "不禁"]
    }
  ],
  "density": 1.234,
  "has_issues": false
}
```

## Usage

```python
from agents.evaluation.skills.slop_detect.skill import Skill

skill = Skill()
result = await skill.execute({"text": "..."}, model_router=None)
```
