---
name: constraint_disambiguate
description: 分析创作设定中的模糊、不完整或矛盾之处，生成针对性的细化问题。
---

# 约束消歧

## 说明

基于 StoryArchitect（Disambiguator）的 LLM 技能。分析用户提供的世界书、
角色卡和大纲，识别其中模糊、不完整或可能矛盾的约束条件，并生成针对性的
澄清问题，帮助用户完善创作设定。

## 输入

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| world_book | string | Yes | 世界书内容 |
| character_cards | string | Yes | 角色卡内容 |
| outline | string | Yes | 大纲内容 |
| reference_context | string | No | 参考作品信息，用于辅助判断 |

## 输出

| 字段 | 类型 | 说明 |
|------|------|------|
| analysis | string | 对设定问题的整体分析 |
| questions | array | 澄清问题列表，每项含 category / question / reason / priority |
