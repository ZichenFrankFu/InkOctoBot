---
name: quality_score
description: 基于规则的复合质量评分聚合器，将多维度评分加权汇总为最终分数，无需LLM调用。
---

# 质量评分

## 说明

Rule-based composite quality scoring aggregation. Accepts individual
dimension scores and computes a weighted overall score using predefined
weights per dimension. Determines whether the text passes a configurable
quality threshold. Operates without LLM calls.

Dimension weights:
- constraint_satisfaction: 0.25
- consistency: 0.20
- knowledge_isolation: 0.15
- repetition: 0.10
- style_quality: 0.15
- pacing: 0.10
- slop_free: 0.05

## 输入

| Field            | Type   | Required | Description                                 |
|------------------|--------|----------|---------------------------------------------|
| dimension_scores | object | yes      | Mapping of dimension name to score (0-100)  |

## 输出

```json
{
  "overall_score": 78.5,
  "dimension_scores": {
    "consistency": 90.0,
    "repetition": 70.0
  },
  "passed": true
}
```
