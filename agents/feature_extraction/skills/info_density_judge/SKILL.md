---
name: info_density_judge
display_name: 信息密度判断
description: 判断一章/一段文本的信息密度（推进剧情、抛设定、铺垫的有效信息量），输出 0–1 浮点分值并说明理由；灌水越多分值越低。
version: 1.0.0
model_role: reference_extractor
tags: [feature_extraction, info_density, pacing, judgement]
permissions: []
---

## Description

特征提取技能之一。评估一段文本的「信息密度」——单位篇幅里推进剧情、
抛出设定、做铺垫的有效信息量。过渡 / 灌水章给低分，关键章给高分。

## Input

| Field | Type   | Required | Description        |
|-------|--------|----------|--------------------|
| text  | string | yes      | 待评估的章节文本   |

## Output

```json
{ "info_density": 0.72, "reason": "本章推进主线并揭示一处伏笔，信息量较高。" }
```
