---
name: shuangdian_extract
display_name: 爽点提取
description: 从网文文本中提取"爽点"（满足感/兴奋点），包括爽点类型、位置、强度及触发机制。
version: 1.0.0
model_role: analyzer
tags: [analysis, shuangdian, extraction, webnovel]
permissions: [read_narrative]
---

## Description

从输入的网络小说文本中，识别并提取"爽点"（读者满足感/兴奋点）。包括但不限于：
- 打脸爽点（face-slapping）：主角反击轻视者
- 升级爽点（power-up）：实力突破、获得宝物
- 装逼爽点（show-off）：展示隐藏实力、震惊众人
- 复仇爽点（revenge）：以牙还牙、惩罚反派
- 逆袭爽点（comeback）：绝境翻盘、扭转乾坤
- 认亲爽点（reunion）：身份揭露、贵人相认
- 碾压爽点（domination）：以绝对实力碾压对手
- 收获爽点（harvest）：大量获取资源、奖励

对每个爽点分析触发机制、强度、读者情感共鸣点。

使用 LLM 进行深度语义分析，能够识别隐含的爽点结构和节奏编排。

## Input

| 字段   | 类型   | 必填 | 说明           |
| ------ | ------ | ---- | -------------- |
| text   | string | 是   | 待分析的网文文本 |

## Output

返回 JSON 对象，包含以下字段：

```json
{
  "shuangdian": [
    {
      "type": "爽点类型",
      "subtype": "子类型（如有）",
      "text_span": "原文片段",
      "position": "文本中的大致位置（百分比）",
      "intensity": "high | medium | low",
      "trigger": "触发机制描述",
      "emotional_payoff": "读者情感回报描述"
    }
  ],
  "summary": {
    "total_count": 0,
    "type_distribution": {"打脸": 2, "升级": 1},
    "pacing_assessment": "爽点节奏评价",
    "density_score": 0.0
  }
}
```
