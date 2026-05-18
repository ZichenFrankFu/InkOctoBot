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
    "reference.characters": {
        "template": """[自动化数据抽取 · 不是对话] 你的输出会被 `json.loads` 直接解析；任何非 JSON 字符都会导致管线失败。

从下面这一**卷**的小说文本中提取主要角色。

作品上下文（仅供消歧 / 检索）：
- 作品标题：《{title}》
- 作者：{author}
- 平台：{platform}
- 本卷：第 {volume_index} 卷 {volume_title}
- 包含章节：第 {start_chapter}–{end_chapter} 章（共 {n_chapters} 章）

**严格禁止**（违反则整条响应被视为错误）：
- 任何寒暄、解释、对话语句（如「你好」「让我告诉你」「这个故事讲的是」「如果你想了解...」）
- ```json ... ``` 这样的 markdown 包装
- <think>...</think> 等推理块
- JSON 之外的任何文字、引导句、结束语

**只输出**：以 `[` 开始、以 `]` 结束的合法 JSON 数组。找不到任何角色就返回 `[]`。

每个角色一个对象，字段：
- name: 角色姓名/称呼（必填，2-4 字最佳，从文本原文摘录）
- role_tag: **一个**字符串，从这 9 个值中选一个：主角 / 女主角 / 男配 / 女配 / 反派 / 师长 / 重要配角 / 路人 / 其他
- intro: 1-3 句客观简介（身份/能力/关键背景），不写主观评价、不剧透
- speech_samples: 最多 3 条对白原文（从文本摘录），无则填 []
- mentions: 整数，出现次数估计
- first_seen_at: 首次出场时间锚点（故事中时间，如「1954 年」），找不到就留空
- first_chapter: 本卷里首次出场的章号，如「第 12 章」；强烈建议填写

输出示例（仅示意结构，不要照抄）：
[{{"name":"庆尘","role_tag":"主角","intro":"穿越者","speech_samples":[],"mentions":120,"first_seen_at":"1954 年春","first_chapter":"第 1 章"}}]

最多 30 个角色，按重要性排序（主角第一）。

本卷正文（约 {n_chars} 字）：
{text}
""",
        "vars": ["title", "author", "platform", "volume_index", "volume_title",
                 "start_chapter", "end_chapter", "n_chapters", "n_chars", "text"],
        "description": "参考作品分卷提取角色（姓名/简介/对白/首次出场时间 + 章节）",
    },

    "reference.settings": {
        "template": """[自动化数据抽取 · 不是对话] 你的输出会被 `json.loads` 直接解析；任何非 JSON 字符都会导致管线失败。

从下面这一**卷**的小说文本中提取世界观设定。

作品上下文（仅供消歧 / 检索）：
- 作品标题：《{title}》
- 作者：{author}
- 平台：{platform}
- 本卷：第 {volume_index} 卷 {volume_title}
- 包含章节：第 {start_chapter}–{end_chapter} 章（共 {n_chapters} 章）

**严格禁止**（违反则整条响应被视为错误）：
- 任何寒暄、解释、对话语句（如「你好」「让我告诉你」「在 18 号监狱里...」）
- ```json ... ``` 这样的 markdown 包装
- <think>...</think> 等推理块
- JSON 之外的任何文字

**只输出**：以 `[` 开始、以 `]` 结束的合法 JSON 数组。找不到任何设定就返回 `[]`。

每条设定一个对象，字段：
- category: 必填，从以下英文 key 选一个：power_system | factions | geography | social_rules | history | hard_rules | worldview | other
- title: 设定名称（如「魔法体系」「皇家骑士团」「时间法则」）
- content: 2-4 句客观描述，写已知事实
- hidden: 可选。该设定背后尚未对读者公开的真相、来源或动机；无则填 ""
- first_introduced_at: 首次出现的故事中时间（如「1954 年」），找不到则留空
- first_chapter: 本卷里首次出现的章号，如「第 5 章」；强烈建议填写

输出示例（仅示意结构）：
[{{"category":"factions","title":"18 号监狱","content":"一个收押超凡个体的特殊机构。","hidden":"","first_introduced_at":"","first_chapter":"第 1 章"}}]

最多 25 条。

本卷正文（约 {n_chars} 字）：
{text}
""",
        "vars": ["title", "author", "platform", "volume_index", "volume_title",
                 "start_chapter", "end_chapter", "n_chapters", "n_chars", "text"],
        "description": "参考作品分卷提取设定（世界观/力量体系/势力/地理 ...）",
    },

    "reference.outline": {
        "template": """[自动化数据抽取 · 不是对话] 你的输出会被 `json.loads` 直接解析；任何非 JSON 字符都会导致管线失败。

为下面这一**卷**的小说文本提取**编年史**格式的剧情大纲。

作品上下文（仅用于消歧 / 检索 / 你跟人类对话时引用——不要把它们当成「我们已经聊过的内容」）：
- 作品标题：《{title}》
- 作者：{author}
- 平台：{platform}
- 本卷：第 {volume_index} 卷 {volume_title}
- 包含章节：第 {start_chapter}–{end_chapter} 章（共 {n_chapters} 章）

**严格禁止**（违反则整条响应被视为错误）：
- 任何寒暄、解释、对话语句（如「你好」「这一卷讲的是」「让我告诉你」「庆尘是一个穿越者」）
- ```json ... ``` 这样的 markdown 包装
- <think>...</think> 等推理块
- JSON 之外的任何文字

**只输出**：以 `{{` 开始、以 `}}` 结束的合法 JSON 对象。

输出 JSON schema（字段名严格匹配）：

{{
  "logline": "≤ 50 字一句话概括本卷主线；不写背景介绍",
  "epochs": [
    {{
      "title": "通常就是本卷的故事时间（如「1954 年春」）或本卷名",
      "periods": [
        {{
          "time": "时间段（故事中时间，如「春」「主角入狱后」），与 events[].time_marker 协调",
          "events": [
            {{
              "subject": "事件主语：角色名 / 组织名 / 「叙事者」",
              "category": "plot_main | plot_side | character | setting | conflict | revelation | foreshadow | other",
              "name": "事件名（≤ 12 字）",
              "description": "1-2 句客观描述发生了什么；不写心理、不复述对话原文",
              "hidden": "可选；该事件背后**本卷尚未公开**的真相或动机；无则填空字符串",
              "time_marker": "故事中时间，如「1954 年 3 月」「春末」；无显式时间填 \\"\\"",
              "first_chapter": "本卷里首次出现该事件的章号，如「第 12 章」；强烈建议填写"
            }}
          ]
        }}
      ]
    }}
  ]
}}

要求：
- `time_marker` 是「故事中时间」（fiction-world clock），`first_chapter` 是「该事件在原文里首次出现的章节」（real-text reference）。两者用途不同，请尽量都填。
- 如果一卷里事件密集，把 epoch 切成 2-5 个 periods（按时间或主题）。
- 事件按发生顺序排列。
- 找不到任何事件时返回 `{{"logline":"","epochs":[]}}`。

本卷正文（约 {n_chars} 字）：
{text}
""",
        "vars": ["title", "author", "platform", "volume_index", "volume_title",
                 "start_chapter", "end_chapter", "n_chapters", "n_chars", "text"],
        "description": "参考作品分卷提取编年史大纲（含故事中时间 + 首次出现章节）",
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
- settings 项形态 {{category, title, content, hidden?, first_introduced_at?}}。category 必须为 power_system/factions/geography/social_rules/history/hard_rules/worldview/other 之一。""",
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
