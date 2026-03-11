---
name: rhetoric_classify
display_name: 修辞手法分类
description: 识别并分类文本中的修辞手法，包括比喻、排比、拟人、夸张、反讽等。
version: 1.0.0
model_role: analyzer
tags: [analysis, rhetoric, classification]
permissions: [read_rhetoric]
---

## Description

从输入的文本中，识别并分类所有使用的修辞手法。包括但不限于：
- 比喻（明喻、暗喻、借喻）
- 排比与对仗
- 拟人与拟物
- 夸张
- 反讽与讽刺
- 双关与暗示
- 反复与回环
- 设问与反问
- 通感与移觉

对每个识别到的修辞手法，提供原文位置、类型、效果分析。

使用 LLM 进行深度语义分析，能够识别复杂和嵌套的修辞结构。

## Input

| 字段   | 类型   | 必填 | 说明           |
| ------ | ------ | ---- | -------------- |
| text   | string | 是   | 待分析的文本内容 |

## Output

返回 JSON 对象，包含以下字段：

```json
{
  "devices": [
    {
      "type": "修辞类型",
      "subtype": "子类型（如有）",
      "text_span": "原文片段",
      "explanation": "修辞效果分析",
      "effectiveness": "high | medium | low"
    }
  ],
  "summary": {
    "total_count": 0,
    "type_distribution": {"比喻": 3, "排比": 2},
    "overall_style": "整体修辞风格评价"
  }
}
```
