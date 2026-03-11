---
name: scene_direct
display_name: 场景导演
description: 将章节细纲拆解为分镜场景计划，包含角色指令、知识边界和情绪弧线
version: 1.0.0
model_role: scene_director
max_tokens: 4000
temperature: 0.6
tags: [production, scene, planning]
permissions: [read_worldbook, read_characters]
input_schema:
  type: object
  required: [chapter_outline, chapter_num]
  properties:
    chapter_outline:
      type: string
      description: 章节细纲文本
    chapter_num:
      type: integer
      description: 章节编号
    character_cards:
      type: string
      description: 相关角色卡信息
output_schema:
  type: object
  required: [scenes]
  properties:
    chapter_num:
      type: integer
    scenes:
      type: array
      items:
        type: object
        properties:
          scene_index: { type: integer }
          location: { type: string }
          time: { type: string }
          characters: { type: array, items: { type: string } }
          summary: { type: string }
          beats: { type: array, items: { type: string } }
          character_instructions: { type: object }
          narrator_instructions: { type: string }
    chapter_arc:
      type: string
---

## Description

场景导演技能基于 SceneDirector 代理，负责将章节细纲拆解为结构化的分镜场景计划。每个场景包含地点、时间、在场角色、节拍序列以及针对每个角色的导演指令（情绪状态、秘密目标、知识边界、行为约束）。

## Input

- **chapter_outline** (string, required): 章节细纲文本，描述本章的主要情节走向
- **chapter_num** (integer, required): 章节编号
- **character_cards** (string, optional): 参与本章角色的角色卡信息，用于生成角色级别的导演指令

## Output

JSON 对象，包含：
- **chapter_num**: 章节编号
- **scenes**: 场景数组，每个场景包含 scene_index、location、time、characters、summary、beats、character_instructions、narrator_instructions
- **chapter_arc**: 本章整体情绪弧线描述
