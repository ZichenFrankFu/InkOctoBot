---
name: character_profile
display_name: 角色画像提取
description: 从文本中提取角色画像信息，包括性格特征、人物关系、动机、成长弧线等结构化数据。
version: 1.0.0
model_role: analyzer
tags: [analysis, character, extraction]
permissions: [read_characters]
---

## Description

从输入的小说或故事文本中，识别并提取所有出场角色的画像信息。包括但不限于：
- 基本属性（姓名、外貌、身份）
- 性格特征（主要性格、次要性格）
- 人物关系（与其他角色的关系）
- 动机与目标
- 角色弧线（成长变化）
- 标志性台词或行为

使用 LLM 进行深度语义分析，能够处理隐含信息和间接描写。

## Input

| 字段   | 类型   | 必填 | 说明           |
| ------ | ------ | ---- | -------------- |
| text   | string | 是   | 待分析的文本内容 |

## Output

返回 JSON 对象，包含以下字段：

```json
{
  "characters": [
    {
      "name": "角色名",
      "aliases": ["别名"],
      "appearance": "外貌描述",
      "identity": "身份/职业",
      "personality": {
        "primary_traits": ["主要性格特征"],
        "secondary_traits": ["次要性格特征"]
      },
      "relationships": [
        {
          "target": "对方角色名",
          "type": "关系类型",
          "description": "关系描述"
        }
      ],
      "motivations": ["动机"],
      "arc": "角色弧线描述",
      "signature_quotes": ["标志性台词"]
    }
  ]
}
```
