---
name: repetition_detect
description: 基于规则的重复模式检测，包括词汇级、短语级和结构级重复分析，无需LLM调用。
---

# 重复检测

## 说明

Rule-based repetition detection that identifies word-level, phrase-level,
and structural repetition in text. Operates entirely locally without any
LLM calls, making it fast and cost-free. Detects sentence-start repetition,
repeated phrases, and overly uniform paragraph structures.

## 输入

| Field | Type   | Required | Description                    |
|-------|--------|----------|--------------------------------|
| text  | string | yes      | The text to analyze            |

## 输出

```json
{
  "score": 85,
  "issues": [
    {
      "type": "sentence_start|repeated_phrase|structural",
      "items": [...],
      "description": "..."
    }
  ],
  "has_issues": false
}
```
