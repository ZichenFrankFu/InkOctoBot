---
name: editor_write
display_name: 编辑写作
description: 将角色表演记录和旁白素材剪辑组装为文学化的章节正文
version: 1.0.0
model_role: editor_writer
max_tokens: 8000
temperature: 0.7
tags: [production, writing, assembly]
permissions: [read_worldbook, read_characters]
input_schema:
  type: object
  required: [performance_records, narrator_text, chapter_num]
  properties:
    performance_records:
      type: array
      items: { type: string }
      description: 各场景的角色表演记录列表
    narrator_text:
      type: string
      description: 旁白素材文本
    chapter_num:
      type: integer
      description: 章节编号
    narrative_instructions:
      type: string
      description: 叙事指令（视角、节奏、情绪弧线等）
    style_profile:
      type: string
      description: 风格档案，描述目标写作风格
output_schema:
  type: object
  required: [text]
  properties:
    text:
      type: string
      description: 组装完成的章节正文
---

## Description

编辑写作技能基于 EditorWriter 代理，负责将多个角色的表演记录与旁白素材剪辑、组装为连贯的文学化章节正文。它将半结构化的表演格式（动作标记、对话标记、内心独白）转化为流畅的叙事文本，同时融入环境描写和氛围渲染。

## Input

- **performance_records** (array of string, required): 各场景的表演记录文本列表
- **narrator_text** (string, required): 旁白素材，包含环境描写和氛围渲染
- **chapter_num** (integer, required): 章节编号
- **narrative_instructions** (string, optional): 叙事指令，如视角选择、节奏控制、情绪弧线要求等
- **style_profile** (string, optional): 风格档案，描述目标写作风格特征

## Output

包含 `text` 字段的对象，值为组装完成的章节正文文本。文本中不保留任何表演记录的格式标记，为可直接发布的文学化叙事。
