---
name: style_drift_detect
display_name: 风格漂移检测
description: 基于规则的统计风格偏移检测，将文本特征与目标风格画像进行比较，无需LLM调用。
version: 1.0.0
model_role: none
tags: [evaluation, quality, style, drift, rule-based]
permissions: []
---

## Description

Rule-based statistical style comparison against a target profile. Extracts
features such as average sentence length, dialogue ratio, and paragraph
length, then compares them to the target profile to detect deviations
exceeding a 30% threshold. Operates without LLM calls.

## Input

| Field          | Type   | Required | Description                              |
|----------------|--------|----------|------------------------------------------|
| text           | string | yes      | The text to analyze                      |
| target_profile | object | no       | Target style feature values to compare   |

`target_profile` keys: `avg_sentence_length`, `dialogue_ratio`,
`avg_paragraph_length`, `total_length`.

## Output

```json
{
  "drifted": true,
  "drifts": [
    {
      "feature": "avg_sentence_length",
      "target": 25.0,
      "current": 40.5,
      "deviation": 0.62
    }
  ],
  "features": {
    "avg_sentence_length": 40.5,
    "dialogue_ratio": 0.12,
    "avg_paragraph_length": 150.0,
    "total_length": 3000
  },
  "score": 80
}
```

## Usage

```python
from agents.evaluation.skills.style_drift_detect.skill import Skill

skill = Skill()
result = await skill.execute(
    {
        "text": "...",
        "target_profile": {"avg_sentence_length": 25.0, "dialogue_ratio": 0.15},
    },
    model_router=None,
)
```
