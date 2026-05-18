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
- role_tag: 该角色在本段中的定位标签，从以下选**一个**（找不到合适的就用 "其他"）：
            主角 / 女主角 / 男配 / 女配 / 反派 / 师长 / 重要配角 / 路人 / 其他
            如果文本中明确有一个主视角主角，请把他/她标为「主角」；如果出现明显的恋爱/搭档主线女性角色，标为「女主角」。
- intro: 1-3 句客观简介，包括身份、能力、关键背景；不写主观评价或剧透
- speech_samples: 最多 3 条具有代表性的对白原文（从文本中摘录，不要编造）
- mentions: 该角色在文本中出现的大致次数（整数估计）
- first_seen_at: 该角色首次出场的时间锚点。作品里有显式时间（如「1954 年」「2030 年 2 月」）就照写；
                没有就写所在「第 N 章」；都不便确定时写「约 M 万字处」。**不要编造日期**，找不到就给章节号。

只返回 JSON 数组，不要 markdown、不要解释。最多 30 个角色，按重要性排序（主角第一）。

文本（约 {n_chapters} 章，{n_chars} 字）：
{text}
"""

_SETTINGS_PROMPT = """你是专业的小说分析师。请从下面的小说文本中提取世界观设定（power_system 力量体系、factions 势力组织、geography 地理、social_rules 社会规则、history 历史、hard_rules 硬规则、worldview 世界观、other 其他）。

输出 JSON 列表，每条设定一个对象：
- category: 必填，从以下英文 key 选一个：power_system | factions | geography | social_rules | history | hard_rules | worldview | other
- title: 设定名称（如「魔法体系」「皇家骑士团」「时间法则」）
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


_RHYTHM_V2_PROMPT = """你是专业的小说叙事+节奏分析师。请分析下面的小说文本，输出**一个**合并后的 JSON 对象，
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
    """Extract a JSON payload from an LLM response.

    Handles:
      - DeepSeek R1 / OpenAI o1 / Qwen-thinking style ``<think>…</think>``
        reasoning blocks emitted before the answer (sometimes the closing
        tag is dropped — we also handle a lone leading ``<think>`` by
        skipping past it to the first JSON delimiter).
      - markdown ``` fences (``` or ```json ... ```).
      - free text preceding or trailing the JSON object/array — we lock
        onto the first ``[``/``{`` and trim back to the last ``]``/``}``.
    """
    s = (raw or "").strip()

    # 1) Strip reasoning-token blocks.
    s = re.sub(r"<think>.*?</think>", "", s, flags=re.DOTALL | re.IGNORECASE).strip()
    # If a lone <think> opened but never closed, just delete the tag so
    # the JSON-locator below can still find the payload.
    s = re.sub(r"<think>", "", s, flags=re.IGNORECASE).strip()
    s = re.sub(r"</think>", "", s, flags=re.IGNORECASE).strip()

    # 2) Strip a ```...``` fence if the WHOLE response is wrapped in one.
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", s, re.DOTALL)
    if fence:
        s = fence.group(1).strip()
    # Also handle a partial fence (model started a fence but didn't close).
    inner = re.search(r"```(?:json)?\s*(.+)$", s, re.DOTALL)
    if inner and not s.startswith("[") and not s.startswith("{"):
        s = inner.group(1).strip()
        # If a closing fence appears in the captured body, trim there.
        end = s.find("```")
        if end > 0:
            s = s[:end].strip()

    # 3) Lock onto the earliest JSON start (no 200-char cap — reasoning
    #    prefixes can be much longer than that).
    earliest = -1
    for ch in "[{":
        idx = s.find(ch)
        if idx >= 0 and (earliest < 0 or idx < earliest):
            earliest = idx
    if earliest > 0:
        s = s[earliest:]

    # 4) Trim back to the last closing delimiter (last ] or last }).
    last = -1
    for ch in "}]":
        idx = s.rfind(ch)
        if idx > last:
            last = idx
    if last >= 0:
        s = s[: last + 1]

    return s


_WEB_VERIFY_HINT = (
    "如果你具备联网搜索能力，请优先使用搜索结果对你抽取出的事实"
    "（角色姓名、设定名称、关键事件、时间线）进行验证；对于不能验证、"
    "或与文本表面信息相矛盾的项，**宁可留空也不要编造**。"
)


async def _invoke(router: Any, prompt: str, *,
                    max_tokens: int = 4096,
                    use_web_search: bool = False) -> str:
    """Single entry-point for AI calls. When ``use_web_search`` is True we
    route to the ``reference_web_search`` role and use the provider's
    ``generate_with_web_search`` path; otherwise the regular
    ``reference_extractor`` invoke. The hint is prepended so the model
    knows to cross-check facts when web search is available."""
    if use_web_search:
        full_prompt = f"{_WEB_VERIFY_HINT}\n\n{prompt}"
        raw = await router.invoke_with_web_search(
            role="reference_web_search", prompt=full_prompt,
            max_tokens=max_tokens, temperature=0.2,
        )
    else:
        raw = await router.invoke(
            role=_ROLE, prompt=prompt,
            max_tokens=max_tokens, temperature=0.2,
        )
    raw = raw or ""
    # Diagnostic: log a short snippet so the server log shows whether the
    # model returned anything at all (vs empty string), useful when JSON
    # parsing downstream fails on thinking models.
    try:
        snippet = raw[:200].replace("\n", " ")
        logger.debug("[ai_extractor] raw response (%d chars): %r", len(raw), snippet)
    except Exception:
        pass
    return raw


def _parse_list(raw: str) -> list[dict]:
    stripped = _strip_json(raw)
    try:
        data = json.loads(stripped)
    except json.JSONDecodeError as e:
        raise ValueError(_format_parse_error("list", raw, stripped, e)) from e
    if not isinstance(data, list):
        raise ValueError(f"expected list, got {type(data).__name__}")
    return [x for x in data if isinstance(x, dict)]


def _parse_obj(raw: str) -> dict:
    stripped = _strip_json(raw)
    try:
        data = json.loads(stripped)
    except json.JSONDecodeError as e:
        raise ValueError(_format_parse_error("object", raw, stripped, e)) from e
    if not isinstance(data, dict):
        raise ValueError(f"expected dict, got {type(data).__name__}")
    return data


def _format_parse_error(want: str, raw: str, stripped: str,
                          err: json.JSONDecodeError) -> str:
    """Build a diagnostic that names WHY the response was unparseable. Two
    common failure modes for thinking models (DeepSeek-R1, o1, Qwen-thinking):
      A) The model burns its entire reasoning budget inside <think>…</think>
         and never emits an answer → ``stripped`` is empty.
      B) The model emits prose ("The list is as follows: …") with no JSON
         delimiter at all → ``stripped`` still has no ``[`` / ``{``.
    The snippet makes it actionable instead of just printing "Expecting value"."""
    raw_s = (raw or "").strip()
    if not raw_s:
        return ("AI 返回了空字符串。可能模型超时或未配置；请检查模型连通性与"
                "max_tokens 设置。")
    snippet = raw_s[:300].replace("\n", " ")
    if not stripped:
        # Pure reasoning / no JSON at all
        return (f"AI 返回中未找到任何 JSON（reason 块为空或截断）。"
                f"原始内容片段：{snippet!r}（共 {len(raw_s)} 字符）。"
                f"如果使用 DeepSeek-R1/o1 类 thinking 模型，请提高 max_tokens "
                f"或换用非 thinking 模型。")
    # Has SOMETHING that looked like JSON but failed to parse
    stripped_snip = stripped[:200].replace("\n", " ")
    return (f"AI 返回的 JSON 无法解析（{err.msg} at line {err.lineno} col {err.colno}）"
            f"。已剥离 reasoning 后的内容片段：{stripped_snip!r}（共 {len(stripped)} 字符）。"
            f"期望：{want}。")


# ── Public API ──────────────────────────────────────────────────────


async def ai_extract_characters(chapters: list[dict], router: Any,
                                   *, prompt_override: str | None = None,
                                   use_web_search: bool = False) -> list[dict]:
    """Returns list of {name, mentions, intro, speech_samples, first_seen_at}.
    appearance_chapters / appearance_word_count are filled in by the caller
    after intersecting with chapter texts (rule-based, deterministic)."""
    from analysis.feature_extraction.prompts import render
    text, nchars = _build_segment_text(chapters)
    prompt = render(
        "reference.characters", override=prompt_override,
        n_chapters=len(chapters), n_chars=nchars, text=text,
    )
    raw = await _invoke(router, prompt, use_web_search=use_web_search)
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
            "role_tag": (it.get("role_tag") or "").strip(),
        })
    return out


async def ai_extract_settings(chapters: list[dict], router: Any,
                                *, prompt_override: str | None = None,
                                use_web_search: bool = False) -> list[dict]:
    """Returns list of {category, title, content, hidden, first_introduced_at}."""
    from analysis.feature_extraction.prompts import render
    text, nchars = _build_segment_text(chapters)
    prompt = render(
        "reference.settings", override=prompt_override,
        n_chapters=len(chapters), n_chars=nchars, text=text,
    )
    raw = await _invoke(router, prompt, use_web_search=use_web_search)
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


_CHAPTER_TYPES_VALID = frozenset({
    "日常", "战斗", "高潮", "角色个人回",
    "主线事件", "支线事件", "伏笔铺垫", "收束",
    "转折", "其他",
})


async def ai_extract_rhythm_v2(chapters: list[dict], router: Any,
                                  *, prompt_override: str | None = None,
                                  use_web_search: bool = False) -> dict:
    """Single AI call that produces the consolidated rhythm_json shape
    (replaces ai_extract_narrative + ai_extract_rhythm)."""
    from analysis.feature_extraction.prompts import render
    text, _ = _build_segment_text(chapters)
    prompt = render(
        "reference.rhythm", override=prompt_override,
        n_chapters=len(chapters), text=text,
    )
    raw = await _invoke(router, prompt, max_tokens=4096, use_web_search=use_web_search)
    obj = _parse_obj(raw)

    valid_openings = {"in_medias_res", "dialogue_open", "worldbuilding", "character_intro"}
    op = obj.get("opening_pattern")
    if op not in valid_openings:
        op = "character_intro"

    def _clean_types(raw_types: Any) -> list[str]:
        if not isinstance(raw_types, list):
            return ["其他"]
        out: list[str] = []
        seen: set[str] = set()
        for t in raw_types:
            if not isinstance(t, str):
                continue
            s = t.strip()
            if s in _CHAPTER_TYPES_VALID and s not in seen:
                seen.add(s); out.append(s)
        return out or ["其他"]

    chap_feats_raw = obj.get("chapter_features") or []
    chapter_features: list[dict] = []
    for cf in chap_feats_raw:
        if not isinstance(cf, dict):
            continue
        chapter_features.append({
            "chapter": int(cf.get("chapter") or 0),
            "types": _clean_types(cf.get("types")),
            "info_density": float(cf.get("info_density") or 0.0),
            "summary": str(cf.get("summary") or "").strip()[:200],
            "hooks": [
                {
                    "position": str(h.get("position") or "段中"),
                    "content": str(h.get("content") or "").strip()[:80],
                }
                for h in (cf.get("hooks") or [])
                if isinstance(h, dict)
            ],
        })

    return {
        "coverage": {
            "chapters": len(chapters),
            "chars": sum(len(ch.get("content") or "") for ch in chapters),
        },
        "opening_pattern": op,
        "climax_positions": [int(x) for x in (obj.get("climax_positions") or []) if isinstance(x, (int, float))],
        "shuangdian": [
            {"chapter": int(s.get("chapter") or 0), "type": str(s.get("type") or "other")}
            for s in (obj.get("shuangdian") or []) if isinstance(s, dict)
        ],
        "chapter_features": chapter_features,
        "info_density_curve": [
            float(x) for x in (obj.get("info_density_curve") or []) if isinstance(x, (int, float))
        ],
        "pacing_segments": [
            {
                "start": int(s.get("start") or 1),
                "end": int(s.get("end") or 1),
                "pacing": str(s.get("pacing") or "medium"),
                "avg_info_density": float(
                    s.get("avg_info_density") or s.get("avg_tension") or 0.5
                ),
            }
            for s in (obj.get("pacing_segments") or []) if isinstance(s, dict)
        ],
    }
