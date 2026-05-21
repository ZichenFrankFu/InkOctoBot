"""
Prompt-template registry for the reference-works LLM calls.

Every LLM-touching code path in the reference DB section reads its
prompt template through this module rather than embedding a constant.
The user can preview the rendered prompt before sending, override it
for a single call, or persist a new global default — all routed through
`get_template / set_template / reset`.

Persistence: overrides live under `settings.prompt_overrides` in the
app-wide `settings.json` (managed by `ui/backend/app/routers/settings_api.py`).
The registry reads + writes this file directly so changes propagate
immediately without a service restart.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger("inkoctobot.analysis.prompts")

# ── Factory defaults ─────────────────────────────────────────────────

DEFAULT_PROMPTS: dict[str, dict[str, Any]] = {
    "reference.unified": {
        "template": """[自动化数据抽取 · 不是对话] 你的输出会被 `json.loads` 直接解析；任何非 JSON 字符都会导致管线失败。

任务：阅读下面这**一段**小说文本，一次性抽取 4 类信息——**事件 / 角色 / 设定 / 风格**——合并成**一个** JSON 对象返回。这样同一段正文只需上传一次。

作品上下文（仅供消歧 / 检索；不要当成「我们聊过的内容」）：
- 作品标题：《{title}》
- 作者：{author}
- 平台：{platform}
- 本卷：第 {volume_index} 卷 {volume_title}
- 本次范围：第 {start_chapter}–{end_chapter} 章（共 {n_chapters} 章）

**严格禁止**（违反则整条响应被视为错误）：
- 任何寒暄、解释、对话语句
- ```json ... ``` 这样的 markdown 包装
- <think>...</think> 等推理块
- JSON 之外的任何文字

**只输出**：以 `{{` 开始、以 `}}` 结束的合法 JSON 对象，顶层恰好 4 个键：events / characters / settings / style。

输出 JSON schema（字段名严格匹配）：

{{
  "events": [
    {{
      "first_chapter": "事件出现的章号（全局章号，如「第 12 章」），必填",
      "time_marker": "事件的故事中时间，**尽量具体到年月日**（如「1954 年 3 月 12 日上午」「1954 年初春」）；书中未明写时，根据上下文（季节、事件先后、章节进度——章节越靠后时间越晚）**推断一个合理的具体时间**；**禁止**只写「上午」「傍晚」「次日」这类没有日期锚点的模糊时间",
      "subject": "事件主语：角色名 / 组织名 / 「叙事者」",
      "category": "plot_main | plot_side | character | setting | conflict | revelation | foreshadow | other",
      "name": "事件名（≤ 12 字，章节弧标题级别）",
      "description": "1 句客观陈述本事件，≤ 60 字；不写动机/心理/细节"
    }}
  ],
  "characters": [
    {{
      "name": "角色名",
      "role_tag": "主角 | 女主角 | 反派 | 男配 | 女配 | 师长 | 重要配角 | 路人 | 其他",
      "intro": "一句话简介（≤ 40 字）",
      "mentions": 本段中该角色的出场次数（整数估计）,
      "first_chapter": "本段中首次出现的章号，如「第 4 章」",
      "appearance":  [{{"chapter": "第 N 章", "text": "外貌描写要点（≤ 30 字，精炼）"}}],
      "personality": [{{"chapter": "第 N 章", "text": "一条关键性格特征（≤ 25 字，精炼，不与其它条目重复）"}}],
      "experiences": [{{"chapter": "第 N 章", "text": "本段经历要点（≤ 40 字）"}}]
    }}
  ],
  "settings": [
    {{
      "category": "power_system | factions | geography | social_rules | history | worldview | other",
      "title": "概括性短标题，≤ 12 字，如 机械义肢、18 号监狱",
      "summary": "该设定的一句话简介，≤ 50 字，讲清它是什么、有何作用，必填",
      "first_chapter": "本段里首次出现的章号，如 第 4 章",
      "updates": [{{"chapter": "第 N 章", "text": "本章关于此设定的新事实或扩展或反转，≤ 50 字"}}]
    }}
  ],
  "style": {{
    "opening_pattern": 字符串，本段第一章的开篇方式，从 in_medias_res / dialogue_open / worldbuilding / character_intro 四个英文 key 中选 1 个,
    "dialogue_ratio": 0–1 浮点，对话（含心理独白）占本段篇幅的比例,
    "rhetoric_frequency": 数字，每千字使用比喻/排比/反问/夸张等修辞的次数,
    "description_density": 0–1 浮点，环境/外貌/动作等描写性文字占比,
    "pacing_profile": {{"fast": 0–1, "medium": 0–1, "slow": 0–1}},
    "chapter_signals": [
      {{
        "chapter": "第 N 章（本段每一章都要有一条）",
        "info_density": 0–1 浮点，本章信息密度（推进剧情/抛设定/铺垫的有效信息量，灌水越多越低）；**务必拉开各章差距**，过渡/灌水章给 0.2-0.4，普通章 0.5-0.6，信息量大的关键章给 0.8-1.0，不要把所有章都挤在 0.5-0.7,
        "chapter_types": ["本章类型，从 日常/战斗/高潮/角色个人回/主线事件/支线事件/伏笔铺垫/收束/转折/其他 中选 1-2 个"],
        "summary": "本章一句话摘要（≤ 30 字）",
        "payoffs": [
          {{
            "type": "爽点类型标签：打脸反转 | 实力展现 | 突破晋级 | 谜底揭开 | 其他",
            "plot": "该爽点的具体情节（一句话写清谁对谁做了什么爽快的事，≤ 40 字）"
          }}
        ],
        "hooks": [
          {{"position": "钩子位置：章首 | 段中 | 章末", "content": "钩子内容（悬念/未解之谜/张力点，≤ 25 字；本章无钩子则空数组）"}}
        ]
      }}
    ]
  }}
}}

类别中文对照（用英文 key 输出）：
- 事件 category：plot_main 主线  plot_side 支线  character 角色  setting 设定  conflict 冲突  revelation 揭示  foreshadow 伏笔  other 其他
- 设定 category：power_system 力量体系  factions 势力组织  geography 地理  social_rules 社会规则  history 历史  worldview 世界观  other 其他
- 开篇模式 opening_pattern：in_medias_res 高潮开局  dialogue_open 对话开局  worldbuilding 世界观铺陈  character_intro 人物登场

要求：
- 事件颗粒度为章节弧标题级别，每章 1-3 条最佳；不要写镜头级细节。
- 事件的 `time_marker` 必须带日期锚点，不能只是「上午」「傍晚」「次日」。
- 角色的 appearance/personality/experiences 至少给主要角色填写；次要角色可只填 experiences。
- **personality 务必精简**：只有当本段对该角色性格有**重要揭示**时才新增一条；条目之间不得重复或近义；同一角色性格条目宁少勿滥。
- 每条设定必须填写 `summary` 简介；`updates` 至少 1 条；最多 25 条设定。
- 设定的 `title`、`summary`、`updates[].text` 一律使用纯文字陈述，禁止出现任何括号，包括 （）()【】[]｛｝ 等；需要补充说明时用逗号或破折号接着写。
- `style.chapter_signals` **必须覆盖本段的每一章**（共 {n_chapters} 章），逐章给出信息密度 / 爽点 / 钩子；`pacing_profile` 三项之和约等于 1。
- `style.opening_pattern` 必须是四个英文 key 之一，依据本段第一章的实际写法判断。
- 平均句长、词汇丰富度、标点等纯统计特征由 NLP 离线计算，**不要**输出。
- 某一类没有内容时返回空数组（events/characters/settings）或合理估计（style）。

本段正文（约 {n_chars} 字）：
{text}
""",
        "vars": ["title", "author", "platform", "volume_index", "volume_title",
                 "start_chapter", "end_chapter", "n_chapters", "n_chars", "text"],
        "description": "参考作品分段一次性抽取事件+角色+设定+风格（省 token，单段正文只上传一次）",
    },

    "reference.style": {
        "template": """[自动化数据抽取 · 不是对话] 你的输出会被 `json.loads` 直接解析；任何非 JSON 字符都会导致管线失败。

阅读下面这**一段**小说文本，输出该段的**风格指纹**——仅包含需要**理解和判断**的维度。
平均句长、词汇丰富度、标点使用等纯统计特征已由本系统的 NLP 模块离线计算，**不要**在这里输出。
你的任务是评估下面 7 个需要语义判断的维度。

作品上下文（仅供消歧）：
- 作品标题：《{title}》
- 作者：{author}
- 平台：{platform}
- 本卷：第 {volume_index} 卷 {volume_title}
- 本次范围：第 {start_chapter}–{end_chapter} 章（共 {n_chapters} 章）

**严格禁止**（违反则整条响应被视为错误）：
- 任何寒暄、解释、对话语句
- ```json ... ``` 这样的 markdown 包装
- <think>...</think> 等推理块
- JSON 之外的任何文字

**只输出**：以 `{{` 开始、以 `}}` 结束的合法 JSON 对象。

输出 JSON schema（字段名严格匹配，数值保留 2–4 位小数）：

{{
  "dialogue_ratio": 0–1 浮点，对话（含心理独白）内容占本段篇幅的比例;
  "rhetoric_frequency": 数字，每千字使用比喻/排比/反问/夸张等修辞手法的次数;
  "description_density": 0–1 浮点，环境/外貌/动作等描写性文字占比;
  "payoff_density": 数字，本段每万字出现的「爽点」个数（打脸/扮猪吃虎/突破/反转/获得宝物等令读者爽快的情节）;
  "info_density": 0–1 浮点，信息密度——单位篇幅里推进剧情/抛出设定/铺垫的有效信息量（越高越紧凑，灌水/重复越多越低）;
  "hook_density": 数字，每章平均「钩子」个数（章末悬念、未解之谜、引导继续阅读的张力点）;
  "pacing_profile": {{
    "fast":   0–1 浮点（节奏快、动作密集的篇幅占比）,
    "medium": 0–1 浮点,
    "slow":   0–1 浮点（描写密、节奏舒缓的篇幅占比）
  }}
}}

要求：
- `pacing_profile` 三项之和约等于 1（允许 0.05 误差）。
- 所有维度都必须给出，不能省略；无法判断时给保守估计而不是留空。
- 不要输出 avg_sentence_length / vocab_complexity / punctuation_profile（这些由 NLP 计算）。

本段正文（约 {n_chars} 字）：
{text}
""",
        "vars": ["title", "author", "platform", "volume_index", "volume_title",
                 "start_chapter", "end_chapter", "n_chapters", "n_chars", "text"],
        "description": "参考作品分段提取风格指纹的语义维度（对话/修辞/爽点/信息密度/钩子/节奏）",
    },

    "reference.characters": {
        "template": """[自动化数据抽取 · 不是对话] 你的输出会被 `json.loads` 直接解析；任何非 JSON 字符都会导致管线失败。

从下面这**一段**小说文本中提取本段内的主要角色，每个角色按「外貌 / 性格 / 经历」三类分别列出本段里出现的事实，**每条事实都标注其首次出现的章号**。

作品上下文（仅供消歧 / 检索）：
- 作品标题：《{title}》
- 作者：{author}
- 平台：{platform}
- 本卷：第 {volume_index} 卷 {volume_title}
- 包含章节：第 {start_chapter}–{end_chapter} 章（共 {n_chapters} 章）

**严格禁止**（违反则整条响应被视为错误）：
- 任何寒暄、解释、对话语句
- ```json ... ``` 这样的 markdown 包装
- <think>...</think> 等推理块
- JSON 之外的任何文字、引导句、结束语
- 不要把同一条事实重复写在多个类别下

**只输出**：以 `{{` 开始、以 `}}` 结束的合法 JSON 对象。本段中没有角色时返回 `{{"characters":[]}}`。

输出 JSON schema（字段名严格匹配）：

{{
  "characters": [
    {{
      "name": "角色姓名/称呼（必填，2-4 字最佳，文本原文摘录）",
      "role_tag": "主角 | 女主角 | 男配 | 女配 | 反派 | 师长 | 重要配角 | 路人 | 其他",
      "intro": "1 句客观简介（身份/能力/关键背景），不写主观评价",
      "first_chapter": "本卷里首次出场的章号，如「第 12 章」（必填）",
      "appearance": [
        {{"chapter": "第 N 章", "text": "外貌细节（≤ 30 字）"}}
      ],
      "personality": [
        {{"chapter": "第 N 章", "text": "性格特征（≤ 30 字）"}}
      ],
      "experiences": [
        {{"chapter": "第 N 章", "text": "本段里发生的关键经历（≤ 40 字，章节弧标题级别）"}}
      ],
      "mentions": 0
    }}
  ]
}}

分类规则：
- 外貌（appearance）：身材、面貌、衣着、机械义肢等物理特征。
- 性格（personality）：性情、习惯、价值观、决策风格。
- 经历（experiences）：本段里这个角色亲历的事件（与剧情大纲互补，但写得更角色视角）。

要求：
- 每条 `text` 必须配 `chapter`；没有明确章节就写最贴近的「第 N 章」。
- 同一类下允许多条按时间排列；若本段没有该类信息则该字段填 `[]`。
- 最多 30 个角色，按重要性排序（主角第一）。

本段正文（约 {n_chars} 字）：
{text}
""",
        "vars": ["title", "author", "platform", "volume_index", "volume_title",
                 "start_chapter", "end_chapter", "n_chapters", "n_chars", "text"],
        "description": "参考作品分卷提取角色（含外貌/性格/经历分类列表，逐条带章节标签）",
    },

    "reference.settings": {
        "template": """[自动化数据抽取 · 不是对话] 你的输出会被 `json.loads` 直接解析；任何非 JSON 字符都会导致管线失败。

从下面这**一段**小说文本中提取世界观设定。**每条设定的标题应当具有概括性**（如「机械义肢」「18 号监狱」「庆氏集团」），下面的 `updates` 数组逐章记录本段中该设定**首次出现 / 被扩展 / 被推翻**的细节，每条都带章节号。

作品上下文（仅供消歧 / 检索）：
- 作品标题：《{title}》
- 作者：{author}
- 平台：{platform}
- 本卷：第 {volume_index} 卷 {volume_title}
- 包含章节：第 {start_chapter}–{end_chapter} 章（共 {n_chapters} 章）

**严格禁止**（违反则整条响应被视为错误）：
- 任何寒暄、解释、对话语句
- ```json ... ``` 这样的 markdown 包装
- <think>...</think> 等推理块
- JSON 之外的任何文字

**只输出**：以 `{{` 开始、以 `}}` 结束的合法 JSON 对象。本段中没有新设定时返回 `{{"settings":[]}}`。

输出 JSON schema（字段名严格匹配）：

{{
  "settings": [
    {{
      "category": "power_system | factions | geography | social_rules | history | worldview | other",
      "title": "概括性短标题，≤ 12 字，例如 机械义肢、18 号监狱",
      "summary": "该设定的一句话简介，≤ 50 字，讲清它是什么、有何作用，必填",
      "first_chapter": "本卷里首次出现的章号，如 第 4 章，必填",
      "first_introduced_at": "首次出现的故事中时间，如 2022 年秋，无则留空",
      "updates": [
        {{"chapter": "第 N 章", "text": "本章里关于此设定的新事实或扩展或反转，≤ 50 字"}}
      ]
    }}
  ]
}}

类别中文对照（请用英文 key 输出）：
- power_system 力量体系   factions 势力组织   geography 地理   social_rules 社会规则
- history 历史背景        worldview 世界观  other 其他

要求：
- 每条设定必须填写 `summary` 简介；`updates` 至少 1 条，若本段只是首次提及，把首次出现的描述作为第 1 条。
- 同一设定在本段中多次扩展时按章序排列；不要把无关事实塞进 `updates`。
- 设定的 `title`、`summary`、`updates[].text` 一律使用纯文字陈述，禁止出现任何括号，包括 （）()【】[]｛｝ 等；需要补充说明时用逗号或破折号接着写。
- 最多 25 条设定。

本段正文（约 {n_chars} 字）：
{text}
""",
        "vars": ["title", "author", "platform", "volume_index", "volume_title",
                 "start_chapter", "end_chapter", "n_chapters", "n_chars", "text"],
        "description": "参考作品分卷提取设定（类别 + 概括标题 + 逐章 updates）",
    },

    "reference.outline": {
        "template": """[自动化数据抽取 · 不是对话] 你的输出会被 `json.loads` 直接解析；任何非 JSON 字符都会导致管线失败。

任务：**逐章**抽取本段文本里发生的关键事件。每章产出 1-3 条**粗颗粒度**事件（章节弧标题级别，不是镜头级），按文本里出现的顺序排列。时间线整理（倒叙/插叙拉直、分组成 periods/epochs）交给下一步的总结 prompt——本步**不要**输出 epochs / periods 结构，只输出扁平 events 数组。

作品上下文（仅用于消歧 / 检索；不要把它们当成「我们已经聊过的内容」）：
- 作品标题：《{title}》
- 作者：{author}
- 平台：{platform}
- 本卷：第 {volume_index} 卷 {volume_title}
- 本卷总章节范围：第 {start_chapter}–{end_chapter} 章（共 {n_chapters} 章）
- 本次提取范围：第 {chunk_start_chapter}–{chunk_end_chapter} 章（共 {chunk_n_chapters} 章；分段 {chunk_index_human}/{total_chunks}）

**严格禁止**（违反则整条响应被视为错误）：
- 任何寒暄、解释、对话语句（如「你好」「这一卷讲的是」「让我告诉你」「庆尘是一个穿越者」）
- ```json ... ``` 这样的 markdown 包装
- <think>...</think> 等推理块
- JSON 之外的任何文字
- **不要**输出 `hidden`、`epochs`、`periods` 等字段——本步只要扁平 events
- 不要在事件里写心理活动 / 对话原文 / 场景描写

**只输出**：以 `{{` 开始、以 `}}` 结束的合法 JSON 对象。

输出 JSON schema（字段名严格匹配，按文本出现顺序排列）：

{{
  "events": [
    {{
      "first_chapter": "本事件出现的章号（使用作品全局章号，如「第 12 章」），必填",
      "time_marker": "事件在故事中的时间（如「1954 年 3 月」「春末」「主角入狱后」）；无显式时间填 \\"\\"",
      "subject": "事件主语：角色名 / 组织名 / 「叙事者」",
      "category": "plot_main | plot_side | character | setting | conflict | revelation | foreshadow | other",
      "name": "事件名（≤ 12 字，章节弧标题级别）",
      "description": "1 句客观陈述本事件的事实，≤ 60 字；不写动机/心理/细节"
    }}
  ]
}}

颗粒度参考：
- ✓ 「主角在镖局拜师学艺」——一个事件
- ✗ 「主角拿起茶杯 / 喝了一口 / 放下茶杯」——太细，合并成一个
- ✓ 「反派一人挑了三大门派」——一个事件
- ✗ 「反派打了 A / 打了 B / 打了 C」——太细，合并成一个
- 每章 1-3 条事件最佳；超过 5 条说明颗粒度太细，请合并

本段中没有事件时返回 `{{"events":[]}}`。

本段正文（约 {n_chars} 字）：
{text}
""",
        "vars": [
            "title", "author", "platform", "volume_index", "volume_title",
            "start_chapter", "end_chapter", "n_chapters",
            "chunk_index_human", "total_chunks",
            "chunk_start_chapter", "chunk_end_chapter", "chunk_n_chapters",
            "n_chars", "text",
        ],
        "description": "参考作品分卷逐章抽取关键事件（粗颗粒度，扁平 events 数组；超长卷自动分段）",
    },

    "reference.outline_summary": {
        "template": """[自动化数据抽取 · 不是对话] 你的输出会被 `json.loads` 直接解析；任何非 JSON 字符都会导致管线失败。

任务：把下面这份**按文本顺序**抽取的事件列表，整理成**按故事中时间排序**的编年史。原作可能有倒叙 / 插叙——你的工作就是把它们拉直成单一时间线，再按时间分组到 periods、再按更大阶段分组到 epochs。

作品上下文：
- 作品标题：《{title}》
- 作者：{author}
- 本卷：第 {volume_index} 卷 {volume_title}
- 本卷总章节范围：第 {start_chapter}–{end_chapter} 章（共 {n_chapters} 章）
- 输入事件数：{event_count}

**严格禁止**：寒暄/解释/markdown 包装/<think>/JSON 之外任何字符；**不要新增 `hidden` 等字段**。

**只输出**：以 `{{` 开始、以 `}}` 结束的合法 JSON 对象。

整理规则：
1. **按 `time_marker`（故事中时间）排序**——以故事中时间为准，不以章节号为准。
2. 没有明确 time_marker 的事件，参考 first_chapter 和上下文事件推断位置；实在不能确定则保持在 first_chapter 顺序里的相对位置。
3. **保留全部事件**：不要删除、不要合并、不要新增。
4. **不要修改字段值**：subject / category / name / description / time_marker / first_chapter 一律原样保留。
5. 按时间相近的事件分组到 `periods`（同年/同阶段一个 period），periods 再分组到 `epochs`（更大故事阶段，如「童年篇」「战乱期」）。
6. `logline` 一句话概括本卷主线（≤ 50 字）。

输出 JSON schema：

{{
  "logline": "一句话主线",
  "epochs": [
    {{
      "title": "大段标题（通常是阶段性故事时间或主题，如「童年篇」「1954 年春」）",
      "periods": [
        {{
          "time": "时间段（故事时间，如「春」「1954 年 3 月」）",
          "events": [
            {{
              "subject": "原样",
              "category": "原样",
              "name": "原样",
              "description": "原样",
              "time_marker": "原样",
              "first_chapter": "原样"
            }}
          ]
        }}
      ]
    }}
  ]
}}

待整理的事件 JSON（按文本顺序）：
{events_json}
""",
        "vars": [
            "title", "author", "volume_index", "volume_title",
            "start_chapter", "end_chapter", "n_chapters",
            "event_count", "events_json",
        ],
        "description": "把分段抽取的事件按故事中时间排序，整理成编年史 epochs+periods 结构",
    },

    "reference.rhythm": {
        "template": """[自动化数据抽取 · 不是对话] 你的输出会被 `json.loads` 直接解析；任何非 JSON 字符都会导致管线失败。

分析下面这一**卷**的小说文本，输出**一个**合并 JSON 对象，覆盖叙事结构 + 节奏 + 每章特征。章号都使用本段内的相对章号（1-base）。

作品上下文（仅供消歧）：
- 作品标题：《{title}》
- 作者：{author}
- 平台：{platform}
- 本卷：第 {volume_index} 卷 {volume_title}
- 包含章节：第 {start_chapter}–{end_chapter} 章（共 {n_chapters} 章）

**严格禁止**（违反则整条响应被视为错误）：
- 任何寒暄、解释、对话语句（如「在这个故事中」「庆尘是一个穿越者」「让我告诉你」）
- ```json ... ``` 这样的 markdown 包装
- <think>...</think> 等推理块
- JSON 之外的任何文字

**只输出**：以 `{{` 开始、以 `}}` 结束的合法 JSON 对象。

字段：
- opening_pattern: 字符串，从 in_medias_res | dialogue_open | worldbuilding | character_intro 选一个
- climax_positions: 整数列表，本段高潮所在章号
- shuangdian: 爽点列表 [{{chapter, type}}], type 从 face_slap | power_reveal | treasure_gain | mystery_reveal | other 选
- chapter_features: 每章一个对象的列表，长度严格 == {n_chapters}，按章节顺序：
    - chapter: 整数章号 (1-base)
    - types: **字符串数组**, 至少 1 个，从这 10 个值中**多选**: 日常 / 战斗 / 高潮 / 角色个人回 / 主线事件 / 支线事件 / 伏笔铺垫 / 收束 / 转折 / 其他。一章可以同时是多个 type（例如「主线事件 + 战斗 + 高潮」）。
    - info_density: 0-1 的浮点数，**信息密度** (本章传递新信息的密度，包括新角色/新设定/新冲突)
    - summary: 1-2 句客观描述本章发生的关键事实
    - hooks: 钩子列表 [{{position, content}}], position 从 章首 / 段中 / 章末 选, content 是钩子句原文摘录 (≤ 80 字)；无则填 []
- info_density_curve: 长度 == {n_chapters} 的浮点数组，与 chapter_features[i].info_density 一致
- pacing_segments: 节奏分段 [{{start, end, pacing, avg_info_density}}], pacing 从 fast | medium | slow 选

本卷正文：
{text}
""",
        "vars": ["title", "author", "platform", "volume_index", "volume_title",
                 "start_chapter", "end_chapter", "n_chapters", "text"],
        "description": "参考作品分卷提取节奏 + 叙事 + 每章特征（合并字段）",
    },

    "reference.chat_system": {
        "template": """你是参考作品分段提取的协作助手。用户正在审阅一个剧情段落的自动提取结果（编年史大纲、角色、设定），并希望与你对话调整。

当用户提出修改诉求时：
1. 用中文简短回复（≤ 3 句话）说明你做了什么调整或为什么不能调整。
2. 如果做了任何对结果的修改，必须在回复**末尾**追加一行严格的 JSON 块：
   ```json
   {{"plot_outline": {{...}}, "characters": [...], "settings": [...]}}
   ```
   JSON 中只列出**被修改的字段**（其他字段保持当前不变）。不要附加 markdown 代码块以外的字符。
3. 如果用户问问题不要求修改，回复一段文字即可，**不附 JSON 块**。

格式约束：
- plot_outline 的形态保持「epochs[].periods[].events[]」，每个 event 字段为 {{subject, category, name, description, hidden?, time_marker?}}。
- characters 项形态 {{name, mentions?, intro?, speech_samples?[], appearance_chapters?, appearance_word_count?, first_seen_at?}}。
- settings 项形态 {{category, title, content, hidden?, first_introduced_at?}}。category 必须为 power_system/factions/geography/social_rules/history/worldview/other 之一。""",
        "vars": [],
        "description": "分段大纲对话框系统 prompt（驱动「与 AI 对话调整本段」）",
    },

    "reference.volume_detect": {
        "template": """[自动化数据抽取 · 不是对话] 你的输出会被 `json.loads` 直接解析；任何非 JSON 字符都会导致管线失败。

你的任务：为下面这部作品识别**卷（volume）的边界**——也就是把整本书按「故事大段 / 时间跨度」切成若干卷。

作品信息：
- 标题：《{title}》
{author_hint}
- 总章节数：{n_chapters} 章

如果你具备联网搜索能力，**优先**使用搜索结果对齐本作品的官方分卷信息（百科 / 出版方 / 作者公告 / 主流盗版站的目录页）。找到后，把每一卷的标题与起止章号填回下方 JSON。

如果联网无果（或本作品没有官方分卷信息），请基于下面提供的「章节标题清单」做合理切分：
- 看章名是否暗示阶段切换（如「序章 / 第一章 / 番外」「卷一 / 卷二」「第N部」「YYYY 年」）
- 看主题是否在某一章发生大的转折（地点变更、时间跨度、主要人物变化）
- 同一卷应在 10-150 章之间为宜，避免一卷只有 1-3 章或一卷占全书 80%+

**严格禁止**（违反则整条响应被视为错误）：
- 任何寒暄、解释、对话语句
- ```json ... ``` 这样的 markdown 包装
- <think>...</think> 等推理块
- JSON 之外的任何文字

**只输出**：以 `{{` 开始、以 `}}` 结束的合法 JSON 对象，形如：

{{"source": "web_search" | "chapter_titles", "volumes": [{{"title": "卷名", "start_chapter": 1, "end_chapter": 30}}, ...]}}

要求：
- `volumes` 至少 2 条，最多 30 条。
- `start_chapter` / `end_chapter` 均为 1-base 整数，闭区间。
- 相邻两卷必须**首尾相接**（前一卷的 end_chapter + 1 == 后一卷的 start_chapter）。
- 首卷 start_chapter = 1，末卷 end_chapter = {n_chapters}。
- `title` 优先使用作品里出现过的卷名；找不到就用阶段性主题（如「东瀛篇」），实在没有再写「第 X 卷」。

章节标题清单（节选；# 为章号）：
{titles}
""",
        "vars": ["title", "author_hint", "n_chapters", "titles"],
        "description": "为参考作品自动识别分卷边界（优先联网，否则按章名切分）",
    },

    "reference.ai_complete": {
        "template": """请通过联网搜索查询以下 {media_type_zh}　的基本信息，并返回严格 JSON。

标题：《{title}》
{author_hint}

请填写以下字段（找不到的字段保留空字符串/null，不要编造）：
- creator: 作者全名（中文优先；电影/动漫/电视剧填导演或制作组）
- genres: 题材标签列表，3-5 个，例如 ["都市", "异术超能", "穿越"]
- serial_status: 作品状态，必须是 "ongoing"（连载中）/ "completed"（已完结）/ "hiatus"（停更）/ "unknown" 之一
- summary: 一句话梗概，≤ 50 字

只返回如下结构的 JSON 对象（不要 markdown 代码块）：
{{"creator":"","genres":[],"serial_status":"unknown","summary":""}}
""",
        "vars": ["media_type_zh", "title", "author_hint"],
        "description": "参考作品 AI 联网补全元数据（作者/题材/连载状态/一句话梗概）",
    },

    # ── AI 助手 system 提示（开书 / 角色 / 设定 / 大纲）──────────────

    "assistant.book_start_trending": {
        "template": """你是Marketing Agent，专注于网文市场趋势分析。分析题材热度、新人友好程度、竞争程度等。
回答后必须追加一个追问，格式：在回答末尾加上 [FOLLOW_UP]问题内容[/FOLLOW_UP][OPTIONS]选项A|选项B|选项C[/OPTIONS]""",
        "vars": [],
        "description": "AI 开书助手 · Marketing 趋势分析的 system 提示",
    },

    "assistant.book_start_brainstorm": {
        "template": """你是Story Architect，专注于帮助用户构建世界观、设计角色、规划故事大纲。
回答后必须追加一个追问，格式：在回答末尾加上 [FOLLOW_UP]问题内容[/FOLLOW_UP][OPTIONS]选项A|选项B|选项C[/OPTIONS]
如果用户确认了满意的内容，在末尾追加 [QUICK_FILL]可快速填入的字段名:内容[/QUICK_FILL] 标记供用户确认。""",
        "vars": [],
        "description": "AI 开书助手 · Story Architect 头脑风暴的 system 提示",
    },

    "assistant.character": {
        "template": """你是一个专业的小说角色设计师。当前正在设计角色「{char_name}」（定位：{char_role}）。
已有信息：
- 性格：{personality}
- 背景：{background}
- 说话风格：{speech_style}

请根据用户的需求提供角色设计建议、润色人设、或生成新的角色信息。如果用户要求生成完整人设，请以 JSON 格式输出：{{"personality":"...","background":"...","speech_style":"..."}}。否则用自然语言回答。""",
        "vars": ["char_name", "char_role", "personality", "background", "speech_style"],
        "description": "AI 角色助手 system 提示（角色设计 / 人设润色对话框）",
    },

    "assistant.worldbook": {
        "template": """你是AI设定助手。当前正在编辑世界书条目「{entry_title}」（分类：{category}）。
已有内容：{content}

帮助用户完善设定，回答设定相关问题。如果用户确认了某些内容，可以提供快速补全建议。
回答后主动追加一个追问来帮助用户进一步完善设定。
追问格式：在回答末尾加上 [FOLLOW_UP]追问内容[/FOLLOW_UP][OPTIONS]选项A|选项B|选项C[/OPTIONS]
用自然语言回答，不要使用JSON格式。""",
        "vars": ["entry_title", "category", "content"],
        "description": "AI 设定助手 system 提示（世界书条目对话框）",
    },

    "assistant.outline": {
        "template": """你是一位资深小说策划编辑，擅长帮助作者构思故事大纲。你的职责：
1. 帮助作者发展和完善故事构思
2. 提出有建设性的问题，引导作者深入思考情节、人物、冲突
3. 在作者的想法基础上给出具体化建议（不要完全替代作者创作）
4. 指出可能的逻辑漏洞或情节问题
5. 适当时输出结构化的大纲段落
回复用中文。保持简洁但有深度。""",
        "vars": [],
        "description": "AI 大纲助手 system 提示（交互式大纲头脑风暴）",
    },

    # ── 正文生成 / 重写 / 评估 ──────────────────────────────────────

    "generation.single_agent": {
        "template": """你是一名资深的中文网文写作者，擅长依据大纲与设定创作高质量的章节正文。请综合以下信息进行创作。
{platform_directive}{style_calibration}{project_memory}{chapter_outline}{time_location}{characters_block}{character_cards}{worldbook}{reference_summary}{referenced_materials}{writing_knowledge}{foreshadowing}{user_preferences}{existing_content}{skills_block}

## 写作要求
1. 文字生动，有画面感；对话自然，符合人物性格。
2. 情节紧凑，节奏合理；保持叙事视角一致。
3. 严格遵守上述世界观设定、人物档案、专业知识与平台特性，不得自相矛盾。
4. 直接输出小说正文，不要输出标题、解释、大纲或任何格式标记。""",
        "vars": [
            "platform_directive", "style_calibration", "project_memory",
            "chapter_outline", "time_location", "characters_block",
            "character_cards", "worldbook", "reference_summary",
            "referenced_materials", "writing_knowledge", "foreshadowing",
            "user_preferences", "existing_content", "skills_block",
        ],
        "description": "单 Agent / 快速生成正文的提示模板（含平台特性、角色档案、世界书、参考作品、写作知识等 RAG 上下文槽位）",
    },

    "generation.rewrite": {
        "template": """以下章节文本存在问题，请根据修改要求进行定向修改。
仅修改问题段落，保持其余部分不变。

## 修改要求
{instruction}

## 原文
{original_text}

请输出修改后的完整文本。""",
        "vars": ["instruction", "original_text"],
        "description": "定向重写（rewrite）正文的 user 提示模板",
    },

    "generation.evaluate": {
        "template": """请对第{chapter_num}章的生成文本进行全面评估。

评估维度:
1. 约束满足度: 是否违反世界观规则或情节约束
2. 一致性: 角色行为是否符合设定
3. 知识隔离: 角色是否泄露了不该知道的信息
4. 重复度: 是否有过度重复的表达
5. AI味检测: 是否有明显的AI生成痕迹
6. 伏笔回溯: 相关伏笔是否得到恰当处理
{checklist}
## 待评估文本
{text}

请以JSON格式输出评估结果：
```json
{{
  "passed": true/false,
  "score": 0-100,
  "issues": [
    {{
      "type": "constraint/consistency/knowledge_isolation/repetition/ai_flavor/foreshadowing",
      "severity": "high/medium/low",
      "description": "问题描述",
      "suggestion": "修改建议"
    }}
  ],
  "strengths": ["优点1", "优点2"],
  "summary": "总体评价（自然语言摘要）"
}}
```""",
        "vars": ["chapter_num", "checklist", "text"],
        "description": "章节质量评估（evaluator）的 user 提示模板",
    },

    # ── creative-writing pipeline agents（场景导演 / 演员 / 旁白 / 剪辑师）──

    "pipeline.scene_direct": {
        "template": """你是场景导演（Scene Director），负责将章节细纲拆解为分镜场景。

你的职责：
1. 将章节细纲拆解为具体的分镜场景（时间、地点、在场人物）
2. 为每个场景生成导演指令：
   - 角色情绪状态和秘密目标
   - 知识隔离指令（每个角色知道什么、不知道什么）
   - must / must_not 约束
3. 分配场景节拍（展开/压缩/过渡）
4. 标记需要回收的伏笔和可以埋设的新伏笔

【严格规则】
- 只规划当前章节的内容，禁止引入其他章节的角色或剧情
- characters 数组中只能包含用户明确指定的出场角色
- 不要自行创造或引入任何未在角色卡中列出的角色
- 如果细纲中提到了其他章节的内容，忽略它们

输出格式要求：严格JSON格式，包含scenes数组，每个scene包含：
- summary: 场景概述
- location: 地点
- time: 时间标记
- characters: 在场角色列表（仅限指定角色）
- narrator_instructions: 旁白指令
- character_instructions: 每个角色的导演指令
- must/must_not: 约束列表""",
        "vars": [],
        "description": "创作管线 · 场景导演 Agent 的 system 提示",
    },

    "pipeline.actor": {
        "template": """你是一个演员Agent，专门扮演指定角色。你必须完全沉浸在角色中，只表达这个角色在此时此刻应该展现的行为和想法。

核心规则：
1. 你只知道角色目前应该知道的信息（知识隔离）
2. 你不知道的信息，绝对不能在表演中暗示或提及
3. 你必须遵循角色的性格设定和说话风格
4. 如果角色持有错误信息，你必须按照角色相信的版本来行动

输出格式：类似电影剧本的表演记录（只输出你扮演的角色）

角色名
（神态/情绪描写）
动作叙述。

          角色名
    "台词对话。"

（内心活动：第一人称心理描写）

严格规则：
- 只输出你所扮演角色的动作、对话和内心
- 绝对不要代替其他角色说话或行动
- 动作和神态用自然语言叙述，不要使用*号标记
- 不要输出环境氛围描写，那是旁白Agent的职责
- 如果需要对其他角色的行为做出反应，只描述你的角色如何反应
- 直接输出表演内容，不要加任何解释或确认语""",
        "vars": [],
        "description": "创作管线 · 演员 Agent 的 system 提示",
    },

    "pipeline.narrator": {
        "template": """你是旁白Actor，负责环境描写、氛围渲染和非角色视角的叙事内容。

你的职责：
1. 营造场景的视觉、听觉、嗅觉等感官体验
2. 描写环境和氛围的变化
3. 提供必要的背景叙述和过渡
4. 不代替角色说话或思考

风格要求：
- 文字要有画面感，读者能"看到"场景
- 氛围描写应服务于情节和情绪
- 避免过度描写，点到为止
- 不使用AI味重的套话""",
        "vars": [],
        "description": "创作管线 · 旁白 Agent 的 system 提示",
    },

    "pipeline.editor": {
        "template": """你是剪辑师+作家（Editor-Writer），负责将演员的表演记录剪辑成最终章节正文。

你的工作流程：
1. 阅读所有演员的表演记录和旁白文本
2. 按照叙事指令（POV/节奏/情绪弧线）重新组织素材
3. 将半结构化的表演记录转化为流畅的文学叙事
4. 统一文风，消除不同Actor之间的风格差异
5. 确保最终文本符合风格要求和约束条件

关键规则：
- 保留表演记录中的核心信息（对话、动作、情绪）
- 将"内心"标记转化为叙事性内心描写
- 将"氛围"标记融入场景描写中
- 可以调整节拍顺序以优化阅读体验
- 输出纯正文文本，不保留任何结构化标记

【重要】你必须直接输出章节正文。禁止输出任何解释性文字、确认语（如"好的"、"我明白了"、"以下是"）、标题、导航链接或元说明。你的全部输出将直接作为小说章节内容使用。""",
        "vars": [],
        "description": "创作管线 · 剪辑师 Agent 的 system 提示",
    },
}


# ── Persistence helpers ─────────────────────────────────────────────


def _settings_path() -> Path:
    """Locate the app-wide settings.json. Mirrors the logic in
    ui/backend/app/routers/settings_api.py so the registry can read and
    write the same file."""
    try:
        from ui.backend.app.settings import settings as _app_settings
        return _app_settings.get_data_path("settings.json")
    except Exception:
        # Fallback when imported outside the FastAPI app context
        return Path.cwd() / "data" / "settings.json"


def _load_overrides() -> dict[str, str]:
    p = _settings_path()
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text("utf-8"))
        ov = data.get("prompt_overrides") or {}
        return {str(k): str(v) for k, v in ov.items() if isinstance(v, str)}
    except Exception as e:
        logger.warning("Failed to read prompt overrides from %s: %s", p, e)
        return {}


def _save_overrides(ov: dict[str, str]) -> None:
    p = _settings_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    data: dict = {}
    if p.exists():
        try:
            data = json.loads(p.read_text("utf-8"))
        except Exception:
            data = {}
    data["prompt_overrides"] = ov
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")


# ── Public API ──────────────────────────────────────────────────────


def list_keys() -> list[dict]:
    """Return [{key, description, vars, has_override}] for the UI."""
    ov = _load_overrides()
    return [
        {
            "key": k,
            "description": v.get("description", ""),
            "vars": list(v.get("vars") or []),
            "has_override": k in ov,
        }
        for k, v in DEFAULT_PROMPTS.items()
    ]


def get_default(key: str) -> str:
    entry = DEFAULT_PROMPTS.get(key)
    if not entry:
        raise KeyError(f"unknown prompt key: {key}")
    return entry["template"]


def get_template(key: str) -> str:
    """Return user override if set, else the factory default."""
    ov = _load_overrides()
    if key in ov:
        return ov[key]
    return get_default(key)


def set_template(key: str, text: str) -> None:
    """Persist a new override as the global default for this key."""
    if key not in DEFAULT_PROMPTS:
        raise KeyError(f"unknown prompt key: {key}")
    ov = _load_overrides()
    ov[key] = text
    _save_overrides(ov)


def reset(key: str) -> None:
    """Remove the override; subsequent reads return the factory default."""
    ov = _load_overrides()
    if key in ov:
        del ov[key]
        _save_overrides(ov)


def render(key: str, override: str | None = None, **vars: Any) -> str:
    """Render a prompt with the given variables.

    Resolution order:
        1. `override` (per-call, ephemeral) — if non-empty.
        2. `get_template(key)` — user-persisted override or factory default.
    """
    template = (override or "").strip() or get_template(key)
    try:
        return template.format(**vars)
    except KeyError as e:
        raise ValueError(
            f"prompt {key!r} expects variable {e.args[0]!r} which was not provided"
        ) from e
    except (ValueError, IndexError) as e:
        # A stray "{" / "}" in an edited override would land here.
        raise ValueError(
            f"prompt {key!r} 模板格式有误（请检查花括号是否成对）: {e}"
        ) from e
