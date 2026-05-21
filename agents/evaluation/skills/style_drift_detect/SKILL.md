---
name: style_drift_detect
description: 基于规则的统计风格偏移检测，将文本特征与目标风格画像进行比较，无需LLM调用。
---

# 风格漂移检测

## 说明

Rule-based statistical style comparison against a target profile. Extracts
features such as average sentence length, dialogue ratio, and paragraph
length, then compares them to the target profile to detect deviations
exceeding a 30% threshold. Operates without LLM calls.

## 输入

| Field          | Type   | Required | Description                              |
|----------------|--------|----------|------------------------------------------|
| text           | string | yes      | The text to analyze                      |
| target_profile | object | no       | Target style feature values to compare   |

`target_profile` keys: `avg_sentence_length`, `dialogue_ratio`,
`avg_paragraph_length`, `total_length`.

## 输出

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
