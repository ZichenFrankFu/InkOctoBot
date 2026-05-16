"""
AI-based feature extraction.

Uses the project's ModelRouter to extract structured information from
chapter text. Each extractor returns the same shape as the corresponding
NLP rule-based extractor, so callers can drop-in replace.

The router is invoked with role="reference_extractor"; configure that
role in config/models.yaml if you want a specific model. Falls back to
the router's default provider otherwise.

All functions are async. On any failure (no API key, parse error, etc.)
they raise — caller should fall back to the NLP extractor.
"""
from __future__ import annotations
import json
import logging
import re
from typing import Any

logger = logging.getLogger("inkoctobot.analysis.ai_extractor")

_ROLE = "reference_extractor"
_MAX_PROMPT_CHARS = 32_000   # cap input size to keep latency reasonable

# ── Prompts ────────────────────────────────────────────────────────

_CHARACTERS_PROMPT = """你是专业的小说分析师。请从下面的小说文本中提取主要角色。

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
"""

_SETTINGS_PROMPT = """你是专业的小说分析师。请从下面的小说文本中提取世界观设定（power_system 力量体系、factions 势力组织、geography 地理、social_rules 社会规则、history 历史、hard_rules 硬规则、worldview 世界观、other 其他）。

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
"""

_NARRATIVE_PROMPT = """你是专业的小说结构分析师。请分析下面的小说文本，输出 JSON 对象描述其叙事结构：

- opening_pattern: 字符串，从 in_medias_res | dialogue_open | worldbuilding | character_intro 选一个
- climax_positions: 整数列表，高潮所在的章节序号（按本段内的相对章号 1-base）
- hook_density: 0 到 1 之间的浮点数，每章末尾使用悬念钩子的密度
- shuangdian: 列表，本段中的「爽点」事件，每项 {{chapter, type}}，type 选 face_slap | power_reveal | treasure_gain | mystery_reveal | other

只返回 JSON 对象，不要 markdown。

文本（约 {n_chapters} 章）：
{text}
"""

_RHYTHM_PROMPT = """你是专业的节奏分析师。请分析下面的小说文本，输出 JSON 对象：

- tension_curve: 长度等于 {n_chapters} 的浮点数列表，按章节顺序，每项 0-1 表示该章的张力强度
- pacing_segments: 节奏分段列表 {{start, end, pacing, avg_tension}}，pacing 选 fast | medium | slow，章号 1-base 闭区间

只返回 JSON 对象，不要 markdown。

文本：
{text}
"""


def _build_segment_text(chapters: list[dict]) -> tuple[str, int]:
    """Concatenate chapter titles + content; truncate to keep prompt size sane.
    Returns (text, n_chars)."""
    parts: list[str] = []
    total = 0
    for ch in chapters:
        title = (ch.get("title") or "").strip()
        content = (ch.get("content") or "").strip()
        block = f"## {title}\n{content}\n"
        if total + len(block) > _MAX_PROMPT_CHARS:
            remaining = _MAX_PROMPT_CHARS - total
            if remaining > 200:
                parts.append(block[:remaining])
            parts.append("\n[...后续章节被省略...]\n")
            total = _MAX_PROMPT_CHARS
            break
        parts.append(block)
        total += len(block)
    return "".join(parts), total


def _strip_json(raw: str) -> str:
    """Strip markdown fences / preambles from an LLM response."""
    s = raw.strip()
    # Remove ```json ... ``` or ``` ... ```
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", s, re.DOTALL)
    if fence:
        s = fence.group(1).strip()
    # If model produced text before the JSON, try to find the first { or [
    for ch in "[{":
        idx = s.find(ch)
        if idx > 0 and idx < 200:
            s = s[idx:]
            break
    # Trim trailing junk after last } or ]
    for ch in "}]":
        idx = s.rfind(ch)
        if idx >= 0:
            s = s[: idx + 1]
            break
    return s


async def _invoke(router: Any, prompt: str, *, max_tokens: int = 4096) -> str:
    raw = await router.invoke(
        role=_ROLE, prompt=prompt,
        max_tokens=max_tokens, temperature=0.2,
    )
    return raw or ""


def _parse_list(raw: str) -> list[dict]:
    data = json.loads(_strip_json(raw))
    if not isinstance(data, list):
        raise ValueError(f"expected list, got {type(data).__name__}")
    return [x for x in data if isinstance(x, dict)]


def _parse_obj(raw: str) -> dict:
    data = json.loads(_strip_json(raw))
    if not isinstance(data, dict):
        raise ValueError(f"expected dict, got {type(data).__name__}")
    return data


# ── Public API ──────────────────────────────────────────────────────


async def ai_extract_characters(chapters: list[dict], router: Any) -> list[dict]:
    """Returns list of {name, mentions, intro, speech_samples} dicts.
    appearance_chapters / appearance_word_count are filled in by the caller
    after intersecting with chapter texts (rule-based, deterministic)."""
    text, nchars = _build_segment_text(chapters)
    prompt = _CHARACTERS_PROMPT.format(
        n_chapters=len(chapters), n_chars=nchars, text=text,
    )
    raw = await _invoke(router, prompt)
    items = _parse_list(raw)
    out: list[dict] = []
    for it in items:
        name = (it.get("name") or "").strip()
        if not name:
            continue
        out.append({
            "name": name,
            "mentions": int(it.get("mentions") or 0),
            "intro": (it.get("intro") or "").strip(),
            "speech_samples": [s for s in (it.get("speech_samples") or []) if isinstance(s, str)][:3],
            "first_seen_at": (it.get("first_seen_at") or "").strip(),
        })
    return out


async def ai_extract_settings(chapters: list[dict], router: Any) -> list[dict]:
    """Returns list of {category, title, content, hidden} dicts."""
    text, nchars = _build_segment_text(chapters)
    prompt = _SETTINGS_PROMPT.format(
        n_chapters=len(chapters), n_chars=nchars, text=text,
    )
    raw = await _invoke(router, prompt)
    items = _parse_list(raw)
    valid_cats = {"power_system", "factions", "geography", "social_rules",
                  "history", "hard_rules", "worldview", "other"}
    out: list[dict] = []
    for it in items:
        title = (it.get("title") or "").strip()
        content = (it.get("content") or "").strip()
        if not title and not content:
            continue
        cat = (it.get("category") or "other").strip()
        if cat not in valid_cats:
            cat = "other"
        out.append({
            "category": cat,
            "title": title,
            "content": content,
            "hidden": (it.get("hidden") or "").strip(),
            "first_introduced_at": (it.get("first_introduced_at") or "").strip(),
        })
    return out


async def ai_extract_narrative(chapters: list[dict], router: Any) -> dict:
    """Returns {opening_pattern, climax_positions, hook_density, shuangdian}."""
    text, _ = _build_segment_text(chapters)
    prompt = _NARRATIVE_PROMPT.format(n_chapters=len(chapters), text=text)
    raw = await _invoke(router, prompt, max_tokens=2048)
    obj = _parse_obj(raw)
    valid_openings = {"in_medias_res", "dialogue_open", "worldbuilding", "character_intro"}
    op = obj.get("opening_pattern")
    if op not in valid_openings:
        op = "character_intro"
    return {
        "opening_pattern": op,
        "climax_positions": [int(x) for x in (obj.get("climax_positions") or []) if isinstance(x, (int, float))],
        "hook_density": float(obj.get("hook_density") or 0.0),
        "shuangdian": [
            {"chapter": int(s.get("chapter") or 0), "type": str(s.get("type") or "other")}
            for s in (obj.get("shuangdian") or []) if isinstance(s, dict)
        ],
        # chapter_beats omitted (AI struggles with this; left empty)
        "chapter_beats": [],
    }


async def ai_extract_rhythm(chapters: list[dict], router: Any) -> dict:
    """Returns {tension_curve, pacing_segments}."""
    text, _ = _build_segment_text(chapters)
    prompt = _RHYTHM_PROMPT.format(n_chapters=len(chapters), text=text)
    raw = await _invoke(router, prompt, max_tokens=2048)
    obj = _parse_obj(raw)
    return {
        "tension_curve": [float(x) for x in (obj.get("tension_curve") or []) if isinstance(x, (int, float))],
        "pacing_segments": [
            {
                "start": int(s.get("start") or 1),
                "end": int(s.get("end") or 1),
                "pacing": str(s.get("pacing") or "medium"),
                "avg_tension": float(s.get("avg_tension") or 0.5),
            }
            for s in (obj.get("pacing_segments") or []) if isinstance(s, dict)
        ],
    }
