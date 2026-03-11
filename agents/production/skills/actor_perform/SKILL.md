---
name: actor_perform
display_name: 角色扮演
description: 以第一人称视角扮演单个角色，生成包含动作、对话和内心独白的表演记录
version: 1.0.0
model_role: actor_agent
max_tokens: 3000
temperature: 0.8
tags: [production, acting, character]
permissions: [read_worldbook, read_characters]
input_schema:
  type: object
  required: [scene_plan, character_name, character_card]
  properties:
    scene_plan:
      type: object
      description: 场景计划（来自 scene_direct 输出中的单个场景）
    character_name:
      type: string
      description: 扮演的角色名称
    character_card:
      type: string
      description: 角色卡信息
    knowledge_view:
      type: string
      description: 角色可见的知识范围（经信息隔离过滤后）
    previous_beats:
      type: string
      description: 前序表演记录，用于保持连贯性
output_schema:
  type: object
  required: [text]
  properties:
    text:
      type: string
      description: 角色表演记录文本
---

## Description

角色扮演技能基于 ActorAgent 代理，以第一人称视角扮演场景中的单个角色。每个角色实例具有信息隔离，只能访问其知识边界内的信息。输出为半结构化的表演记录，包含动作描写、对话和内心独白。

## Input

- **scene_plan** (object, required): 场景计划对象，包含 summary、location、time、characters、beats、character_instructions 等字段
- **character_name** (string, required): 当前扮演的角色名称
- **character_card** (string, required): 角色卡片详细信息（性格、背景、说话习惯等）
- **knowledge_view** (string, optional): 经过信息隔离过滤后该角色可见的知识内容
- **previous_beats** (string, optional): 之前节拍中其他角色的表演记录，用作反应依据

## Output

包含 `text` 字段的对象，值为角色表演记录文本，格式为：
```
[节拍N]
角色名(情绪): *动作描写* "对话内容"
  内心: 内心独白
```
