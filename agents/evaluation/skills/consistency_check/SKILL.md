---
name: consistency_check
description: 检查生成文本与角色设定、世界观规则、前情事件的一致性，通过LLM分析识别不一致之处。
---

# 一致性检查

## 说明

Checks generated text against character cards, world-building rules, and
previous events for consistency. Uses an LLM evaluator to identify
contradictions in character behavior, world-rule violations, and continuity
errors.

## 输入

| Field             | Type   | Required | Description                        |
|-------------------|--------|----------|------------------------------------|
| text              | string | yes      | The generated text to evaluate     |
| character_cards   | string | no       | Character setting descriptions     |
| world_rules       | string | no       | World-building rules and lore      |
| previous_events   | string | no       | Summary of preceding story events  |

## 输出

```json
{
  "consistent": true,
  "issues": [
    {
      "type": "character|world|continuity",
      "description": "Description of the inconsistency",
      "severity": "high|medium|low"
    }
  ]
}
```
