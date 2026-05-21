---
name: scene_direct
description: 将章节细纲拆解为分镜场景计划，包含角色指令、知识边界和情绪弧线。
---

# 场景导演

## 说明

场景导演技能基于 SceneDirector 代理，负责将章节细纲拆解为结构化的分镜场景计划。每个场景包含地点、时间、在场角色、节拍序列以及针对每个角色的导演指令（情绪状态、秘密目标、知识边界、行为约束）。

## 输入

- **chapter_outline** (string, required): 章节细纲文本，描述本章的主要情节走向
- **chapter_num** (integer, required): 章节编号
- **character_cards** (string, optional): 参与本章角色的角色卡信息，用于生成角色级别的导演指令

## 输出

JSON 对象，包含：
- **chapter_num**: 章节编号
- **scenes**: 场景数组，每个场景包含 scene_index、location、time、characters、summary、beats、character_instructions、narrator_instructions
- **chapter_arc**: 本章整体情绪弧线描述
