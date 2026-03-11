---
name: calibration
display_name: 风格校准
description: 根据创作设定生成短篇校准样本，用于风格方向确认
version: 1.0.0
model_role: default
tags: [planner, calibration, style, llm]
permissions: [read_reference_db]
---

## Description

基于 CalibrationAgent 的 LLM 技能。在进入章节创作循环之前，根据世界书、
角色卡和大纲等设定信息，生成 300-500 字的短篇校准样本，供用户确认风格方向。

支持多种样本类型：开篇、对话场景、动作场景、内心独白，并可生成多个风格变体
以供对比选择。

## Input

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| world_book | string | Yes | 世界书内容（会截取前 1500 字） |
| character_cards | string | Yes | 角色卡内容（会截取前 1500 字） |
| outline | string | Yes | 大纲内容（会截取前 1000 字） |
| sample_type | string | No | 样本类型：`opening`（默认）、`dialogue`、`action`、`inner` |
| style_profile | string | No | 风格要求描述 |
| reference_samples | string | No | 参考风格片段 |
| n_variants | integer | No | 生成变体数量（默认 1，设为 >1 时生成多个风格变体） |

## Output

| 字段 | 类型 | 说明 |
|------|------|------|
| samples | array | 生成的校准样本文本列表 |
| sample_type | string | 使用的样本类型 |
| n_variants | integer | 生成的变体数量 |
