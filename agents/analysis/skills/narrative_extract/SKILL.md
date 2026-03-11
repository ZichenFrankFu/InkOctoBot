---
name: narrative_extract
display_name: 叙事要素提取
description: 从文本中提取叙事要素，包括情节点、叙事弧线、张力曲线、冲突结构等。
version: 1.0.0
model_role: analyzer
tags: [analysis, narrative, extraction]
permissions: [read_narrative]
---

## Description

从输入的小说或故事文本中，识别并提取核心叙事要素。包括但不限于：
- 情节点（plot points）：关键事件、转折点
- 叙事弧线（narrative arcs）：起承转合结构
- 张力曲线（tension curve）：紧张度变化
- 冲突结构（conflicts）：主要冲突与次要冲突
- 悬念与伏笔（hooks & foreshadowing）
- 叙事视角与时间线

使用 LLM 对文本进行深度叙事学分析。

## Input

| 字段   | 类型   | 必填 | 说明           |
| ------ | ------ | ---- | -------------- |
| text   | string | 是   | 待分析的文本内容 |

## Output

返回 JSON 对象，包含以下字段：

```json
{
  "plot_points": [
    {
      "event": "事件描述",
      "type": "inciting_incident | rising_action | climax | falling_action | resolution",
      "position": "文本中的大致位置（百分比）",
      "significance": "重要性说明"
    }
  ],
  "narrative_arc": {
    "structure": "三幕式 | 起承转合 | 英雄之旅 | 其他",
    "stages": [
      {"stage": "阶段名", "summary": "阶段概要"}
    ]
  },
  "tension_curve": [
    {"position": "0-100", "level": "1-10", "reason": "原因"}
  ],
  "conflicts": [
    {
      "type": "internal | interpersonal | societal | environmental",
      "parties": ["冲突方"],
      "description": "冲突描述",
      "status": "unresolved | escalating | resolved"
    }
  ],
  "hooks": ["悬念描述"],
  "foreshadowing": ["伏笔描述"]
}
```
