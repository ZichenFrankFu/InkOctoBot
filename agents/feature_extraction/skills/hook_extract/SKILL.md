---
name: hook_extract
display_name: 钩子识别
description: 识别文本中的「钩子」——章首悬念、段中张力点、章末未解之谜等引导读者继续阅读的张力设计，输出钩子位置与内容。
version: 1.0.0
model_role: reference_extractor
tags: [feature_extraction, hook, suspense, extraction]
permissions: []
---

## Description

特征提取技能之一。识别文本中用来「勾」住读者、驱动继续阅读的悬念 / 张力点，
标注其位置（章首 / 段中 / 章末）与内容。

## Input

| Field | Type   | Required | Description    |
|-------|--------|----------|----------------|
| text  | string | yes      | 待分析的文本   |

## Output

```json
{ "hooks": [ { "position": "章末", "content": "门外传来了不该出现的脚步声。" } ] }
```
