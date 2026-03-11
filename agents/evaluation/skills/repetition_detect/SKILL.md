---
name: repetition_detect
display_name: 重复检测
description: 基于规则的重复模式检测，包括词汇级、短语级和结构级重复分析，无需LLM调用。
version: 1.0.0
model_role: none
tags: [evaluation, quality, repetition, rule-based]
permissions: []
---

## Description

Rule-based repetition detection that identifies word-level, phrase-level,
and structural repetition in text. Operates entirely locally without any
LLM calls, making it fast and cost-free. Detects sentence-start repetition,
repeated phrases, and overly uniform paragraph structures.

## Input

| Field | Type   | Required | Description                    |
|-------|--------|----------|--------------------------------|
| text  | string | yes      | The text to analyze            |

## Output

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

## Usage

```python
from agents.evaluation.skills.repetition_detect.skill import Skill

skill = Skill()
result = await skill.execute({"text": "..."}, model_router=None)
```
