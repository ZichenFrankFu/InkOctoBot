---
name: consistency_check
display_name: 一致性检查
description: 检查生成文本与角色设定、世界观规则、前情事件的一致性，通过LLM分析识别不一致之处。
version: 1.0.0
model_role: evaluator
tags: [evaluation, quality, consistency]
permissions: [read_worldbook, read_characters]
---

## Description

Checks generated text against character cards, world-building rules, and
previous events for consistency. Uses an LLM evaluator to identify
contradictions in character behavior, world-rule violations, and continuity
errors.

## Input

| Field             | Type   | Required | Description                        |
|-------------------|--------|----------|------------------------------------|
| text              | string | yes      | The generated text to evaluate     |
| character_cards   | string | no       | Character setting descriptions     |
| world_rules       | string | no       | World-building rules and lore      |
| previous_events   | string | no       | Summary of preceding story events  |

## Output

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

## Usage

```python
from agents.evaluation.skills.consistency_check.skill import Skill

skill = Skill()
result = await skill.execute(
    {
        "text": "...",
        "character_cards": "...",
        "world_rules": "...",
        "previous_events": "...",
    },
    model_router=router,
)
```
