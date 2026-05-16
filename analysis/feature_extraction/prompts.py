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
        "template": """你是专业的小说分析师。请从下面的小说文本中提取主要角色。

输出 JSON 列表，每个角色一个对象：
- name: 角色姓名/称呼（必填，2-4 字最佳）
- intro: 1-3 句客观简介，包括身份、能力、关键背景；不写主观评价或剧透
- speech_samples: 最多 3 条具有代表性的对白原文（从文本中摘录，不要编造）
- mentions: 该角色在文本中出现的大致次数（整数估计）
- first_seen_at: 该角色首次出场的时间锚点。作品里有显式时间（如「1954 年」「2030 年 2 月」）就照写；
                没有就写所在「第 N 章」；都不便确定时写「约 M 万字处」。**不要编造日期**，找不到就给章节号。

只返回 JSON 数组，不要 markdown、不要解释。最多 30 个角色，按重要性排序。

文本（约 {n_chapters} 章，{n_chars} 字）：
{text}
""",
        "vars": ["n_chapters", "n_chars", "text"],
        "description": "参考作品分段提取角色（姓名/简介/对白/首次出场时间锚点）",
    },

    "reference.settings": {
        "template": """你是专业的小说分析师。请从下面的小说文本中提取世界观设定（power_system 力量体系、factions 势力组织、geography 地理、social_rules 社会规则、history 历史、hard_rules 硬规则、worldview 世界观、other 其他）。

输出 JSON 列表，每条设定一个对象：
- category: 必填，从以下英文 key 选一个：power_system | factions | geography | social_rules | history | hard_rules | worldview | other
- title: 设定名称（如「灵能力」「镇潮部队」）
- content: 2-4 句客观描述，写已知事实
- hidden: 可选。该设定背后在本段中尚未对读者公开的真相、来源或动机
- first_introduced_at: 该设定首次出现的时间锚点。作品里有显式时间就照写；没有就写所在「第 N 章」；
                       都不便确定时写「约 M 万字处」。**不要编造日期**，找不到就给章节号。

只返回 JSON 数组，不要 markdown。最多 25 条。

文本（约 {n_chapters} 章，{n_chars} 字）：
{text}
""",
        "vars": ["n_chapters", "n_chars", "text"],
        "description": "参考作品分段提取设定（世界观/力量体系/势力/地理 ...）",
    },

    "reference.rhythm": {
        "template": """你是专业的小说叙事+节奏分析师。请分析下面的小说文本，输出**一个**合并后的 JSON 对象，
覆盖叙事结构 + 节奏 + 每章特征。章号都使用本段内的相对章号（1-base）。

字段：
- opening_pattern: 字符串，从 in_medias_res | dialogue_open | worldbuilding | character_intro 选一个
- climax_positions: 整数列表，本段高潮所在章号
- shuangdian: 爽点列表 [{{chapter, type}}], type 从 face_slap | power_reveal | treasure_gain | mystery_reveal | other 选
- chapter_features: 每章一个对象的列表，长度严格 == {n_chapters}，按章节顺序：
    - chapter: 整数章号 (1-base)
    - types: **字符串数组**, 至少 1 个，从这 10 个值中**多选**: 日常 / 战斗 / 高潮 / 角色个人回 / 主线事件 / 支线事件 / 伏笔铺垫 / 收束 / 转折 / 其他。一章可以同时是多个 type（例如「主线事件 + 战斗 + 高潮」）。
    - info_density: 0-1 的浮点数，**信息密度** (本章传递新信息的密度，包括新角色/新设定/新冲突；替代过去的「张力」)
    - summary: 1-2 句客观描述本章发生的关键事实
    - hooks: 钩子列表 [{{position, content}}], position 从 章首 / 段中 / 章末 选, content 是钩子句原文摘录 (≤ 80 字)
- info_density_curve: 长度 == {n_chapters} 的浮点数组，与 chapter_features[i].info_density 一致
- pacing_segments: 节奏分段 [{{start, end, pacing, avg_info_density}}], pacing 从 fast | medium | slow 选

只返回 JSON 对象，不要 markdown 包装、不要解释。

文本（{n_chapters} 章）：
{text}
""",
        "vars": ["n_chapters", "text"],
        "description": "参考作品分段提取节奏 + 叙事 + 每章特征（合并字段）",
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
