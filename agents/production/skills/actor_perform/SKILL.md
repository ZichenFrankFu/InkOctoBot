---
name: actor_perform
description: 以第一人称视角扮演单个角色，生成包含动作、对话和内心独白的表演记录。
---

# 角色扮演

## 说明

角色扮演技能基于 ActorAgent 代理，以第一人称视角扮演场景中的单个角色。每个角色实例具有信息隔离，只能访问其知识边界内的信息。输出为半结构化的表演记录，包含动作描写、对话和内心独白。

## 输入

- **scene_plan** (object, required): 场景计划对象，包含 summary、location、time、characters、beats、character_instructions 等字段
- **character_name** (string, required): 当前扮演的角色名称
- **character_card** (string, required): 角色卡片详细信息（性格、背景、说话习惯等）
- **knowledge_view** (string, optional): 经过信息隔离过滤后该角色可见的知识内容
- **previous_beats** (string, optional): 之前节拍中其他角色的表演记录，用作反应依据

## 输出

包含 `text` 字段的对象，值为角色表演记录文本，格式为：
```
[节拍N]
角色名(情绪): *动作描写* "对话内容"
  内心: 内心独白
```
