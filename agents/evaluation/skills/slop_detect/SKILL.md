---
name: slop_detect
description: 基于规则的AI生成文本风格检测，使用模式匹配识别AI常见表达，无需LLM调用。
---

# AI味检测

## 说明

Rule-based AI-flavor (slop) detection using pattern matching against a
configurable library of known AI-generated text patterns. Based on slop
detection research (arXiv:2509.19163). Identifies cliche phrases, cliche
actions, cliche similes, and AI-tell markers. Operates without LLM calls.

## 输入

| Field | Type   | Required | Description             |
|-------|--------|----------|-------------------------|
| text  | string | yes      | The text to analyze     |

## 输出

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
