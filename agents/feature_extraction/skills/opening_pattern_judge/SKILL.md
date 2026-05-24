---
name: opening_pattern_judge
description: 判断作品开篇所采用的叙事手法——高潮开局 / 对话开局 / 世界观铺陈 / 人物登场，输出英文 key 与判断理由。
---

# 开篇模式判断

## 说明

特征提取技能之一。读取作品的开篇文本，判断其开篇方式属于四类之一：
in_medias_res（高潮开局）、dialogue_open（对话开局）、worldbuilding
（世界观铺陈）、character_intro（人物登场）。

## 输入

| Field | Type   | Required | Description        |
|-------|--------|----------|--------------------|
| text  | string | yes      | 作品开篇章节文本   |

## 输出

```json
{ "opening_pattern": "in_medias_res", "reason": "首段即切入追逐战，事后再补叙背景。" }
```
