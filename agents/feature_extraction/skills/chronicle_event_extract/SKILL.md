---
name: chronicle_event_extract
display_name: 编年史事件写作
description: 从小说文本中按时间顺序抽取「编年史」事件条目（主语 / 类别 / 事件名 / 描述 / 时间锚点），用于构建作品的编年史大纲。
version: 1.0.0
model_role: reference_extractor
tags: [feature_extraction, chronicle, event, extraction]
permissions: []
---

## Description

特征提取技能之一。阅读一段小说文本，按故事时间顺序抽取章节弧标题级别的
事件，每条事件带主语、类别、事件名、客观描述与时间锚点。输出可直接用于
编年史大纲。

## Input

| Field       | Type    | Required | Description            |
|-------------|---------|----------|------------------------|
| text        | string  | yes      | 待分析的小说文本       |
| n_chapters  | integer | no       | 本段包含的章节数       |

## Output

```json
{ "events": [ { "subject": "范闲", "category": "plot_main", "name": "初入京都", "description": "范闲奉命进京，初见庆国都城。", "time_marker": "第 1 章" } ] }
```
