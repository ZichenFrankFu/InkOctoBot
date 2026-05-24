---
name: marketing_advice
description: 数据驱动的市场趋势分析，提供选题、类型热度、竞争度建议。
---

# 市场营销建议

## 说明

基于 MarketingAgent 的数据驱动分析技能。通过查询参考数据库中的排行榜快照、
小说元数据和标签信息，为作者提供类型热度、市场趋势、竞争度评估和差异化建议。

本技能为非 LLM 技能（纯规则 + 数据库查询），不依赖大语言模型推理。

## 输入

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| genre | string | Yes | 目标类型/分类（如「都市」「玄幻」） |
| db_path | string | Yes | 参考数据库路径 |
| analysis_type | string | No | 分析类型：`full`（默认）、`heat`、`trend`、`competition`、`tags` |
| lookback_days | integer | No | 趋势分析回溯天数，默认 90 |

## 输出

| 字段 | 类型 | 说明 |
|------|------|------|
| genre | string | 查询的类型名 |
| summary | string | 一句话总结（趋势 + 竞争） |
| advices | array | 建议列表，每项含 category / severity / title / detail |
| raw | object | 原始数据（heat / trend / competition / tags） |
