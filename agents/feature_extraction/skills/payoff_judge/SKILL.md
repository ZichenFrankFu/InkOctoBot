---
name: payoff_judge
display_name: 爽点判断
description: 识别文本中的「爽点」——打脸反转、实力展现、突破晋级、谜底揭开等令读者爽快的情节，输出爽点类型与具体情节。
version: 1.0.0
model_role: reference_extractor
tags: [feature_extraction, payoff, shuangdian, judgement]
permissions: []
---

## Description

特征提取技能之一。从网文文本中识别「爽点」——让读者产生满足感 / 兴奋感的
情节点，归类并写清具体情节。

## Input

| Field | Type   | Required | Description    |
|-------|--------|----------|----------------|
| text  | string | yes      | 待分析的文本   |

## Output

```json
{ "payoffs": [ { "type": "打脸反转", "plot": "主角当众反打嘲讽他的世家子弟。" } ] }
```
