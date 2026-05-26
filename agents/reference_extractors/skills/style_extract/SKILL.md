---
name: style_extract
description: 提取文本的写作风格特征，包括句长统计、对话比例、用词丰富度、段落结构等，基于规则与NLP统计。
---

# 写作风格提取

## 说明

从输入的文本中，通过规则和 NLP 统计方法提取写作风格特征。包括：
- 句长统计（平均句长、句长分布、最长/最短句）
- 对话比例（对话文本 vs 叙述文本）
- 用词丰富度（TTR、hapax legomena 比例）
- 段落结构（平均段落长度、段落数量）
- 标点使用模式
- 叙述视角检测
- 时态使用分布

本技能以规则为主，不依赖 LLM 进行核心计算，但可选择性使用 LLM 进行风格总结。

## 输入

| 字段   | 类型   | 必填 | 说明           |
| ------ | ------ | ---- | -------------- |
| text   | string | 是   | 待分析的文本内容 |

## 输出

返回 JSON 对象，包含以下字段：

```json
{
  "sentence_stats": {
    "count": 0,
    "avg_length": 0.0,
    "min_length": 0,
    "max_length": 0,
    "std_dev": 0.0
  },
  "dialogue_ratio": 0.0,
  "vocabulary": {
    "unique_words": 0,
    "total_words": 0,
    "ttr": 0.0,
    "hapax_ratio": 0.0
  },
  "paragraph_stats": {
    "count": 0,
    "avg_length": 0.0
  },
  "punctuation_profile": {
    "exclamation_ratio": 0.0,
    "question_ratio": 0.0,
    "ellipsis_ratio": 0.0,
    "comma_density": 0.0
  }
}
```
