# 语言学文本特征（基础特征提取 · 开篇章节）

「基础特征提取」页原「开篇章节 NLP 维度」section 升级为 **语言学文本特征**。本文档
给维护者/用户说明新增能力、降级路径，以及如何升级到深度/句法增强路径。

> 设计基线：**纯 jieba 即可全功能运行**（day-1，无需 GPU / torch / LTP）。所有重型
> 依赖都是「可选增强」，缺失时自动降级且行为正确。

## 模块一览

| 模块 | 作用 | 依赖 | 缺失时降级 |
|------|------|------|-----------|
| `linguistics.py` | 词性分布 POS + 平均依存距离 MDD | jieba；MDD 真值需 LTP | MDD 退化为按小句长度的「句式复杂度」估算 |
| `lexical_diversity.py` | 词汇丰富度 MATTR + MTLD | 无（纯 Python） | — |
| `sentiment.py` | DUTIR 词典法七大类情感；预留 BERT/RoBERTa/ERNIE 接口 | 词典内置；深度模型需 torch+权重 | 深度后端不可用 → 词典法 |
| `ner_backend.py` | 人名 NER（PER 抽取）+ 硬件分级 | LTP+torch；否则 jieba `nr` | GPU→CPU→跳过 LTP→jieba 兜底 |
| `name_library.py` | 人名库（全名权威 + 派生姓名 + DF + 标记 + CRUD） | 无 | — |
| `name_refresh.py` | 按 book 去重对新增书增量抽人名 | 经 `ner_backend` | 同上 |
| `naming_patterns.py` | 按题材加权取名规律画像 | 无 | — |

## §1 词性分布 + 句式复杂度（MDD）

- POS 用一次 `posseg` 流（与高频词共用，不重复分词）算动/形/名词占比，并给**读者
  视角**别名：动词→**动作场面**、形容词→**修饰描写密度**、名词→**设定密度**。
- MDD：装了 LTP 时算真平均依存距离；否则用每小句 token 数估算「句式复杂度」，UI 标注
  「估算」。

## §2 词汇丰富度（MATTR + MTLD）

抗文本长度影响的两个指标，UI 合成 0–100 的「用词丰富度」分（仅展示，不参与任何排序）。

## §3 情感分析（DUTIR）

- 默认用大连理工 DUTIR《情感词汇本体》**词典法**标注七大类（乐/好/怒/哀/惧/恶/惊）占比。
- 内置 `resources/sentiment/dutir_seed.txt` 种子；**放入完整本体**：把官方 xlsx 转 TSV
  存为同目录 `dutir.txt`（loader 自动优先加载，支持 21 小类→7 大类归并）。
- **接入深度模型**：`sentiment.DeepSentimentBackend` 已留接口。把本地 BERT/RoBERTa/
  ERNIE 权重目录传给 `get_sentiment_backend("deep", deep_model_dir=...)`，在
  `DeepSentimentBackend.analyze()` 里加载并把 logits 映射到七大类即可，调用方零改动。

## §4 高频词标准 + 人名库

- 频率按**相对频率**（次数 ÷ 总词数，‰）展示；df 带 **2 ≤ df ≤ 60% 总作品数**（跨 ≥2 本、不超过 60% unique
  小说）；显式去**虚词**（助 u/介 p/连 c/代 r/副 d/语气 y/叹 e）+ **哈工大 LTP 停用词**
  （`resources/wordlists/ltp_stopwords.txt`）兜底。
- **剔名护栏**：用人名库剔高频词候选时，若该词同时命中常用词表则**不剔**，且只作用于
  候选小集合、不做全文盲删。
- **人名库**（`person_name_library`）：全名为权威记录，姓/名为按姓氏表+复姓识别可重算
  的派生字段；昵称/复姓/单名打标记（不污染规律统计）；每条附来源作品/热度排名/去重书数
  **DF**（按 book 不按 snapshot）。
- **NER 刷新**：仅对市场库**按 book 去重的新增书**（novel_uid 未处理或开篇 content_hash
  变化）跑 PER 抽取，已处理书 / rank snapshot 更新跳过（`name_extraction_state` 台账）。
  触发：手动 API，或每天一次打开基础特征提取时自动后台刷新。
- **硬件降级路径**（`ner_backend.detect_ner_backend`，复用 `embedding.hardware_detector`）：
  有可用 GPU → LTP/CUDA；否则 CPU 算力够 → LTP/CPU；CPU 不足/测不到算力 → 跳过 LTP，
  仅用**打包种子人名库** + jieba `nr` 兜底。零星新名运行时由 jieba `nr` 实时补充。
- **启用 LTP**：`pip install ltp torch`（GPU 机自动用显卡）。装好后下次刷新即走 LTP。
- **用户纠错回环**：高频词/特征结果里看到人名碎片（「翠翠」属于「李翠翠」），填入完整
  人名提交 → 写人名库（全名进 jieba 词典整体切分）+ 片段进排除集 → 清缓存，下次**重新
  分词重算**高频词，碎片自然消失（**用重分词实现，禁止删子串**）。

## §5 取名规律统计

从人名库按**题材分桶**输出加权画像：姓氏分布、名字用字分布（首/末/全位置）、名长结构
（单名/双名/复姓）、生僻字使用率、名字字对典型组合。

- 权重 = 来源作品在「题材×平台」内 rank/heat 的**百分位**（前排权重大）。
- **按 book 去重** + **单本贡献封顶**（防角色多的爆款独霸分布）。
- UI 展示**加权值**而非原始计数；非标准名排除出规律分布。
- 定位：**描述性**的市场命名惯例，**不作**成功公式或打分依据。

## 资源文件

```
resources/
  sentiment/dutir_seed.txt        # DUTIR 七大类种子词典（可放 dutir.txt 完整本体）
  wordlists/ltp_stopwords.txt     # 哈工大 LTP 停用词（兜底，可编辑+热更新）
  name_library/seed_names.txt     # 预建种子人名库（保证 day-1 剔名覆盖）
```
