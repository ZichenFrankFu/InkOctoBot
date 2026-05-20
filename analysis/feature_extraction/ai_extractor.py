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

# Note: these in-file constants are only used by ai_extract_narrative and
# ai_extract_rhythm (legacy entry points; the main pipeline uses
# prompts.py's "reference.rhythm" template via the registry). Keep them
# in sync with the strict JSON-only style used in prompts.py.

_NARRATIVE_PROMPT = """[自动化数据抽取 · 不是对话] 输出将被 json.loads 直接解析。

分析下面的小说文本，输出**一个**描述叙事结构的 JSON 对象。

只允许以 `{{` 开始、以 `}}` 结束的合法 JSON。禁止任何寒暄、解释、markdown 包装、思考块。

字段：
- opening_pattern: 字符串，从 in_medias_res | dialogue_open | worldbuilding | character_intro 选一个
- climax_positions: 整数列表，高潮所在的章节序号（本段相对章号 1-base）
- hook_density: 0-1 浮点数，每章末尾悬念钩子的密度
- shuangdian: 列表，每项 {{chapter, type}}，type 选 face_slap | power_reveal | treasure_gain | mystery_reveal | other

文本（约 {n_chapters} 章）：
{text}
"""

_RHYTHM_PROMPT = """[自动化数据抽取 · 不是对话] 输出将被 json.loads 直接解析。

分析下面的小说文本，输出**一个**节奏分析 JSON 对象。

只允许以 `{{` 开始、以 `}}` 结束的合法 JSON。禁止任何寒暄、解释、markdown 包装、思考块。

字段：
- tension_curve: 长度等于 {n_chapters} 的浮点数列表，每项 0-1
- pacing_segments: [{{start, end, pacing, avg_tension}}]，pacing 选 fast | medium | slow，章号 1-base 闭区间

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


def build_segment_text_chunks(
    chapters: list[dict],
    max_chars: int = _MAX_PROMPT_CHARS,
    *,
    segment_start_chapter: int = 1,
) -> list[dict]:
    """Split a list of chapters into contiguous chunks where each chunk's
    concatenated text fits within ``max_chars``. Used by the prompt-copy
    UI so the user can run an over-budget volume as multiple separate
    web-LLM calls instead of having the tail silently truncated.

    Each returned dict has::

        {
            "chunk_index":      int,     # 0-based
            "total_chunks":     int,
            "text":             str,     # ready-to-splice prompt body
            "n_chapters":       int,     # chapters in this chunk
            "n_chars":          int,     # text length
            "start_chapter":    int,     # absolute chapter # (1-based)
            "end_chapter":      int,     # absolute chapter # (1-based, inclusive)
        }

    ``segment_start_chapter`` lets the caller report absolute chapter
    numbers in the work even when ``chapters`` is just a slice for one
    volume — set it to the volume's first absolute chapter number.

    A single chapter that itself exceeds ``max_chars`` becomes its own
    chunk (and that chunk's text gets hard-truncated, with a marker).
    """
    if not chapters:
        return []

    chunks: list[list[dict]] = []   # list of [chapters-in-chunk]
    cur: list[dict] = []
    cur_chars = 0
    for ch in chapters:
        body = (ch.get("content") or "").strip()
        title = (ch.get("title") or "").strip()
        # Format we use later in the rendered block; include in size accounting.
        block_size = len(body) + len(title) + 4  # "## " + "\n" + content + "\n"
        if cur and cur_chars + block_size > max_chars:
            chunks.append(cur)
            cur = []
            cur_chars = 0
        cur.append(ch)
        cur_chars += block_size
    if cur:
        chunks.append(cur)

    total = len(chunks)
    out: list[dict] = []
    offset = segment_start_chapter - 1  # convert intra-volume idx → absolute
    chapter_cursor = 0  # advances over `chapters`
    for i, ch_group in enumerate(chunks):
        text, nchars = _build_segment_text(ch_group)
        start_abs = chapter_cursor + 1 + offset
        end_abs = chapter_cursor + len(ch_group) + offset
        chapter_cursor += len(ch_group)
        out.append({
            "chunk_index": i,
            "total_chunks": total,
            "text": text,
            "n_chapters": len(ch_group),
            "n_chars": nchars,
            "start_chapter": start_abs,
            "end_chapter": end_abs,
        })
    return out


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

# A short, blunt system prompt — many models default to "helpful assistant
# mode" and answer in prose ("你好！这个故事讲的是...") instead of JSON.
# The system role tends to be obeyed much more strictly than user-content
# instructions, especially for chat-tuned models.
_JSON_SYSTEM_OBJ = (
    "你是一个 JSON 数据抽取器。无论用户输入什么，你只能输出**一个合法的 JSON 对象**。"
    "禁止任何寒暄、解释、markdown 包装、思考过程、```json 代码块。"
    "禁止输出形如「你好」「这个故事讲的是」「让我告诉你」等任何对话语句。"
    "你的整段响应必须以 `{` 开头、以 `}` 结尾。如果你无法完成抽取，"
    "也必须返回符合 schema 的对象，对应字段留空或填 0/[]，**绝对不要返回自然语言**。"
)
_JSON_SYSTEM_LIST = (
    "你是一个 JSON 数据抽取器。无论用户输入什么，你只能输出**一个合法的 JSON 数组**。"
    "禁止任何寒暄、解释、markdown 包装、思考过程、```json 代码块。"
    "禁止输出形如「你好」「这个故事讲的是」「让我告诉你」等任何对话语句。"
    "你的整段响应必须以 `[` 开头、以 `]` 结尾。如果文本中找不到任何匹配项，"
    "返回 `[]`，**绝对不要返回自然语言**。"
)


async def _invoke(router: Any, prompt: str, *,
                    max_tokens: int = 4096,
                    use_web_search: bool = False,
                    expect: str = "object") -> str:
    """Single entry-point for AI calls. When ``use_web_search`` is True we
    route to the ``reference_web_search`` role and use the provider's
    ``generate_with_web_search`` path; otherwise the regular
    ``reference_extractor`` invoke.

    ``expect`` is "object" or "list" — selects the system prompt and
    decides whether to opportunistically request JSON mode (only objects
    are guaranteed; the list system prompt forces the model to emit ``[``).
    """
    system = _JSON_SYSTEM_LIST if expect == "list" else _JSON_SYSTEM_OBJ
    if use_web_search:
        # Web-search providers don't reliably honor response_format and
        # the search-then-summarize flow can't be system-prompted.
        # Keep the legacy path but still embed the cross-check hint.
        full_prompt = (
            f"{system}\n\n{_WEB_VERIFY_HINT}\n\n{prompt}\n\n"
            "再次提醒：只输出纯 JSON，不要任何其他文字。"
        )
        raw = await router.invoke_with_web_search(
            role="reference_web_search", prompt=full_prompt,
            max_tokens=max_tokens, temperature=0.1,
        )
    else:
        # OpenAI-compatible JSON mode forces well-formed JSON for objects
        # (chat.completions response_format). For list-mode we fall back
        # to a stronger system prompt — JSON mode only accepts object roots.
        kw: dict[str, Any] = {}
        if expect == "object":
            kw["response_format"] = {"type": "json_object"}
        raw = await router.invoke(
            role=_ROLE, prompt=prompt, system=system,
            max_tokens=max_tokens, temperature=0.1, **kw,
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


# Common keys models use when wrapping a list inside an object — needed
# because OpenAI/DeepSeek JSON mode only emits objects, so we may receive
# {"characters": [...]} when we wanted just [...].
_LIST_UNWRAP_KEYS = (
    "items", "data", "result", "results", "list", "characters",
    "settings", "entries", "values", "array",
)


def _parse_listish(raw: str) -> list[dict]:
    """Parse a JSON list, also accepting a single-key object that wraps
    the list (e.g. ``{"items": [...]}``). Strict-JSON modes only emit
    objects; we still want list semantics at the call site."""
    stripped = _strip_json(raw)
    try:
        data = json.loads(stripped)
    except json.JSONDecodeError as e:
        raise ValueError(_format_parse_error("list", raw, stripped, e)) from e
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if isinstance(data, dict):
        # Prefer a known wrapper key, else use the single value if it's a list
        for k in _LIST_UNWRAP_KEYS:
            v = data.get(k)
            if isinstance(v, list):
                return [x for x in v if isinstance(x, dict)]
        if len(data) == 1:
            only_v = next(iter(data.values()))
            if isinstance(only_v, list):
                return [x for x in only_v if isinstance(x, dict)]
    raise ValueError(
        f"expected list (or object wrapping a list), got {type(data).__name__}"
    )


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


# Default fillers when the caller doesn't supply work / volume context.
# The prompts ALL reference these vars; rendering would KeyError without
# defaults, so we provide sane "(未知)" placeholders.
_DEFAULT_CTX = {
    "title": "(未提供)",
    "author": "(未知)",
    "platform": "(未知)",
    "volume_index": "1",
    "volume_title": "(本卷)",
    "start_chapter": "1",
    "end_chapter": "?",
}


def _ctx(work_ctx: dict | None) -> dict:
    """Merge caller-supplied work/volume context with defaults so every
    prompt var present in the template is filled in."""
    out = dict(_DEFAULT_CTX)
    if work_ctx:
        for k in _DEFAULT_CTX:
            v = work_ctx.get(k)
            if v is None or v == "":
                continue
            out[k] = str(v)
    return out


def _norm_tagged_list(raw: Any, max_items: int = 20, max_text: int = 80) -> list[dict]:
    """Coerce a character-list field (appearance / personality / experiences)
    into a list of ``{chapter, text}`` items, dropping malformed entries.
    Tolerates flat strings too — the LLM occasionally outputs bare strings
    when it forgets the chapter tag. They become items with empty chapter."""
    out: list[dict] = []
    if not isinstance(raw, list):
        return out
    for it in raw:
        if isinstance(it, str):
            t = it.strip()[:max_text]
            if t:
                out.append({"chapter": "", "text": t})
        elif isinstance(it, dict):
            t = (it.get("text") or "").strip()[:max_text]
            if not t:
                continue
            out.append({
                "chapter": (it.get("chapter") or "").strip()[:20],
                "text": t,
            })
        if len(out) >= max_items:
            break
    return out


def normalize_ai_style(obj: Any) -> dict:
    """Coerce an LLM/pasted style-fingerprint object into the canonical
    LLM-side shape: dialogue_ratio, rhetoric_frequency, description_density,
    payoff_density, info_density, hook_density, pacing_profile. The
    NLP-side fields (avg_sentence_length, vocab_complexity,
    punctuation_profile) are intentionally NOT produced here — they come
    from compute_nlp_style. Returns {} when obj isn't a dict."""
    if not isinstance(obj, dict):
        return {}
    def _f(k: str, default: float = 0.0) -> float:
        v = obj.get(k)
        try:
            return float(v) if v is not None else default
        except (TypeError, ValueError):
            return default
    pp = obj.get("pacing_profile") or {}
    if not isinstance(pp, dict):
        pp = {}
    return {
        "dialogue_ratio":      round(_f("dialogue_ratio"), 4),
        "rhetoric_frequency":  round(_f("rhetoric_frequency"), 4),
        "description_density": round(_f("description_density"), 4),
        "payoff_density":      round(_f("payoff_density"), 4),
        "info_density":        round(_f("info_density"), 4),
        "hook_density":        round(_f("hook_density"), 4),
        "pacing_profile": {
            "fast":   round(float(pp.get("fast") or 0), 3),
            "medium": round(float(pp.get("medium") or 0), 3),
            "slow":   round(float(pp.get("slow") or 0), 3),
        },
    }


async def ai_extract_style(chapters: list[dict], router: Any,
                            *, prompt_override: str | None = None,
                            use_web_search: bool = False,
                            work_ctx: dict | None = None) -> dict:
    """Run the LLM on a chunk and return the LLM-discriminated half of
    a style fingerprint (dialogue ratio, rhetoric, description density,
    payoff/info/hook density, pacing). The deterministic half
    (sentence length, vocab, punctuation) comes from compute_nlp_style."""
    from analysis.feature_extraction.prompts import render
    text, nchars = _build_segment_text(chapters)
    prompt = render(
        "reference.style", override=prompt_override,
        n_chapters=len(chapters), n_chars=nchars, text=text,
        **_ctx(work_ctx),
    )
    raw = await _invoke(router, prompt, use_web_search=use_web_search, expect="dict")
    return normalize_ai_style(_parse_obj(raw))


_SETTING_CATS = frozenset({
    "power_system", "factions", "geography", "social_rules",
    "history", "hard_rules", "worldview", "other",
})


def _normalize_unified_style(raw: Any, n_chars: int, n_chapters: int) -> dict:
    """Coerce the `style` block of a unified extraction into the canonical
    fingerprint. The LLM gives per-chapter signals (info_density, chapter
    types, summary, payoff list, hook list); the chunk-level payoff /
    hook / info densities are DERIVED from them so there's a single
    source of truth, and the per-chapter signals also feed the rhythm
    section."""
    if not isinstance(raw, dict):
        return {}
    def _f(k: str, d: float = 0.0) -> float:
        v = raw.get(k)
        try:
            return float(v) if v is not None else d
        except (TypeError, ValueError):
            return d
    pp = raw.get("pacing_profile") or {}
    if not isinstance(pp, dict):
        pp = {}
    _HOOK_POS = {"章首", "段中", "章末"}
    signals: list[dict] = []
    total_payoffs = 0
    total_hooks = 0
    info_sum = 0.0
    for s in (raw.get("chapter_signals") or []):
        if not isinstance(s, dict):
            continue
        try:
            info = float(s.get("info_density") or 0)
        except (TypeError, ValueError):
            info = 0.0
        # payoffs / hooks: accept rich object lists; tolerate a bare int
        # (older prompt) or list of strings.
        payoffs: list[dict] = []
        raw_payoffs = s.get("payoffs")
        if isinstance(raw_payoffs, list):
            for p in raw_payoffs:
                if isinstance(p, dict):
                    payoffs.append({
                        "type": (p.get("type") or "其他").strip()[:20],
                        "plot": (p.get("plot") or "").strip()[:80],
                    })
                elif isinstance(p, str) and p.strip():
                    payoffs.append({"type": p.strip()[:20], "plot": ""})
        elif isinstance(raw_payoffs, (int, float)):
            payoffs = [{"type": "其他", "plot": ""}] * int(raw_payoffs)
        hooks: list[dict] = []
        raw_hooks = s.get("hooks")
        if isinstance(raw_hooks, list):
            for h in raw_hooks:
                if isinstance(h, dict):
                    pos = (h.get("position") or "章末").strip()
                    if pos not in _HOOK_POS:
                        pos = "章末"
                    hooks.append({
                        "position": pos,
                        "content": (h.get("content") or "").strip()[:60],
                    })
                elif isinstance(h, str) and h.strip():
                    hooks.append({"position": "章末", "content": h.strip()[:60]})
        elif isinstance(raw_hooks, (int, float)):
            hooks = [{"position": "章末", "content": ""}] * int(raw_hooks)
        ctypes = s.get("chapter_types")
        if not isinstance(ctypes, list):
            ctypes = []
        ctypes = [str(t).strip()[:8] for t in ctypes if str(t).strip()][:3]
        signals.append({
            "chapter": (s.get("chapter") or "").strip()[:20],
            "info_density": round(info, 4),
            "chapter_types": ctypes,
            "summary": (s.get("summary") or "").strip()[:60],
            "payoffs": payoffs,
            "hooks": hooks,
        })
        total_payoffs += len(payoffs)
        total_hooks += len(hooks)
        info_sum += info
    n_sig = len(signals)
    return {
        "dialogue_ratio":      round(_f("dialogue_ratio"), 4),
        "rhetoric_frequency":  round(_f("rhetoric_frequency"), 4),
        "description_density": round(_f("description_density"), 4),
        # Derived chunk aggregates from the per-chapter signals.
        "payoff_density":      round(total_payoffs / max(n_chars / 10000.0, 1.0), 4),
        "hook_density":        round(total_hooks / max(n_chapters, 1), 4),
        "info_density":        round(info_sum / n_sig, 4) if n_sig else 0.0,
        "pacing_profile": {
            "fast":   round(float(pp.get("fast") or 0), 3),
            "medium": round(float(pp.get("medium") or 0), 3),
            "slow":   round(float(pp.get("slow") or 0), 3),
        },
        "chapter_signals": signals,
    }


async def ai_extract_all(chapters: list[dict], router: Any,
                          *, prompt_override: str | None = None,
                          use_web_search: bool = False,
                          work_ctx: dict | None = None) -> dict:
    """One LLM call that extracts events + characters + settings + style
    for a chunk. The chapter text is uploaded ONCE instead of four times
    (one call per feature) — the main token saving for the features tab.

    Returns ``{events, characters, settings, style}`` where each piece
    matches the shape of its single-purpose extractor (ai_extract_outline_events
    / ai_extract_characters / ai_extract_settings) plus the unified
    style block (with per-chapter chapter_signals)."""
    from analysis.feature_extraction.prompts import render
    text, nchars = _build_segment_text(chapters)
    prompt = render(
        "reference.unified", override=prompt_override,
        n_chapters=len(chapters), n_chars=nchars, text=text,
        **_ctx(work_ctx),
    )
    raw = await _invoke(router, prompt, max_tokens=8192,
                          use_web_search=use_web_search, expect="object")
    obj = _parse_obj(raw)

    events: list[dict] = []
    for ev in (obj.get("events") or []):
        n = _normalize_event(ev)
        if n is not None:
            events.append(n)

    characters: list[dict] = []
    for it in (obj.get("characters") or []):
        if not isinstance(it, dict):
            continue
        name = (it.get("name") or "").strip()
        if not name:
            continue
        characters.append({
            "name": name,
            "mentions": int(it.get("mentions") or 0) if str(it.get("mentions") or "0").strip().lstrip("-").isdigit() else 0,
            "intro": (it.get("intro") or "").strip(),
            "speech_samples": [],
            "first_seen_at": (it.get("first_seen_at") or "").strip(),
            "first_chapter": (it.get("first_chapter") or "").strip(),
            "role_tag": (it.get("role_tag") or "").strip(),
            "appearance": _norm_tagged_list(it.get("appearance")),
            "personality": _norm_tagged_list(it.get("personality")),
            "experiences": _norm_tagged_list(it.get("experiences")),
        })

    settings: list[dict] = []
    for it in (obj.get("settings") or []):
        if not isinstance(it, dict):
            continue
        title = (it.get("title") or "").strip()
        updates = _norm_tagged_list(it.get("updates"), max_items=30, max_text=120)
        content = (it.get("content") or "").strip()
        if not content and updates:
            content = updates[0]["text"]
        if not title and not content and not updates:
            continue
        cat = (it.get("category") or "other").strip()
        if cat not in _SETTING_CATS:
            cat = "other"
        settings.append({
            "category": cat,
            "title": title,
            "content": content,
            "updates": updates,
            "first_introduced_at": (it.get("first_introduced_at") or "").strip(),
            "first_chapter": (it.get("first_chapter") or "").strip(),
        })

    style = _normalize_unified_style(obj.get("style"), nchars, len(chapters))

    return {
        "events": events,
        "characters": characters,
        "settings": settings,
        "style": style,
    }


async def ai_extract_characters(chapters: list[dict], router: Any,
                                   *, prompt_override: str | None = None,
                                   use_web_search: bool = False,
                                   work_ctx: dict | None = None) -> list[dict]:
    """Returns a list of rich character dicts:

        {
          name, role_tag, intro, mentions, first_seen_at, first_chapter,
          appearance:   [{chapter, text}, ...],   # 外貌
          personality:  [{chapter, text}, ...],   # 性格
          experiences:  [{chapter, text}, ...],   # 经历
          speech_samples: []  # kept for backward compat (always [])
        }

    The new prompt returns ``{"characters": [...]}`` instead of a bare
    list — the parser accepts either via ``_parse_listish``."""
    from analysis.feature_extraction.prompts import render
    text, nchars = _build_segment_text(chapters)
    prompt = render(
        "reference.characters", override=prompt_override,
        n_chapters=len(chapters), n_chars=nchars, text=text,
        **_ctx(work_ctx),
    )
    raw = await _invoke(router, prompt, use_web_search=use_web_search, expect="list")
    items = _parse_listish(raw)
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
            "first_chapter": (it.get("first_chapter") or "").strip(),
            "role_tag": (it.get("role_tag") or "").strip(),
            # Rich per-category fact lists. The new prompt is REQUIRED
            # to emit these; legacy data may not have them, which is
            # fine — the editor falls back gracefully.
            "appearance": _norm_tagged_list(it.get("appearance")),
            "personality": _norm_tagged_list(it.get("personality")),
            "experiences": _norm_tagged_list(it.get("experiences")),
        })
    return out


async def ai_extract_settings(chapters: list[dict], router: Any,
                                *, prompt_override: str | None = None,
                                use_web_search: bool = False,
                                work_ctx: dict | None = None) -> list[dict]:
    """Returns list of rich setting dicts:

        {category, title, first_introduced_at, first_chapter,
         updates: [{chapter, text}, ...]}

    Legacy `content` is preserved (used by older display code) by
    concatenating the updates if the LLM didn't supply one explicitly."""
    from analysis.feature_extraction.prompts import render
    text, nchars = _build_segment_text(chapters)
    prompt = render(
        "reference.settings", override=prompt_override,
        n_chapters=len(chapters), n_chars=nchars, text=text,
        **_ctx(work_ctx),
    )
    raw = await _invoke(router, prompt, use_web_search=use_web_search, expect="list")
    items = _parse_listish(raw)
    valid_cats = {"power_system", "factions", "geography", "social_rules",
                  "history", "hard_rules", "worldview", "other"}
    out: list[dict] = []
    for it in items:
        title = (it.get("title") or "").strip()
        updates = _norm_tagged_list(it.get("updates"), max_items=30, max_text=120)
        content = (it.get("content") or "").strip()
        if not content and updates:
            # Synthesize legacy `content` from the first update so older
            # display code that hasn't been updated to read `updates`
            # still shows something.
            content = updates[0]["text"]
        if not title and not content and not updates:
            continue
        cat = (it.get("category") or "other").strip()
        if cat not in valid_cats:
            cat = "other"
        out.append({
            "category": cat,
            "title": title,
            "content": content,
            "updates": updates,
            "first_introduced_at": (it.get("first_introduced_at") or "").strip(),
            "first_chapter": (it.get("first_chapter") or "").strip(),
        })
    return out


_OUTLINE_CATS = frozenset({
    "plot_main", "plot_side", "character", "setting",
    "conflict", "revelation", "foreshadow", "other",
})


def _normalize_event(ev: Any) -> dict | None:
    """Coerce a single raw event into the canonical shape, dropping it
    if it has neither name nor description. Strips legacy ``hidden``
    fields silently — we removed [隐] from the chronicle contract."""
    if not isinstance(ev, dict):
        return None
    name = (ev.get("name") or "").strip()
    desc = (ev.get("description") or "").strip()
    if not name and not desc:
        return None
    cat = (ev.get("category") or "other").strip()
    if cat not in _OUTLINE_CATS:
        cat = "other"
    return {
        "subject": (ev.get("subject") or "").strip()[:40],
        "category": cat,
        "name": name[:40],
        "description": desc[:120],
        "time_marker": (ev.get("time_marker") or "").strip(),
        "first_chapter": (ev.get("first_chapter") or "").strip(),
    }


async def ai_extract_outline_events(
    chapters: list[dict], router: Any,
    *, prompt_override: str | None = None,
    use_web_search: bool = False,
    work_ctx: dict | None = None,
) -> list[dict]:
    """Step 1 of the outline pipeline: extract a **flat list** of
    coarse events (1-3 per chapter, chapter-arc level) from a chunk
    of chapters. Returns events in text order (not story-time order).

    Output shape (each event)::

        {"subject", "category", "name", "description",
         "time_marker", "first_chapter"}

    No epochs/periods grouping yet — that's step 2 (outline_summary).
    """
    from analysis.feature_extraction.prompts import render
    text, nchars = _build_segment_text(chapters)
    ctx = _ctx(work_ctx)
    prompt = render(
        "reference.outline", override=prompt_override,
        n_chapters=len(chapters), n_chars=nchars, text=text,
        chunk_index_human=1, total_chunks=1,
        chunk_start_chapter=ctx.get("start_chapter", "?"),
        chunk_end_chapter=ctx.get("end_chapter", "?"),
        chunk_n_chapters=len(chapters),
        **ctx,
    )
    raw = await _invoke(router, prompt, max_tokens=4096,
                          use_web_search=use_web_search, expect="object")
    obj = _parse_obj(raw)
    # Accept either {"events": [...]} (current format) or the legacy
    # {"epochs": [{"periods": [{"events": [...]}]}]} (old format,
    # in case a user-customized prompt still produces it).
    flat: list[dict] = []
    raw_events = obj.get("events")
    if isinstance(raw_events, list):
        for ev in raw_events:
            n = _normalize_event(ev)
            if n is not None:
                flat.append(n)
    elif isinstance(obj.get("epochs"), list):
        for ep in obj["epochs"]:
            for per in (ep.get("periods") or []) if isinstance(ep, dict) else []:
                for ev in (per.get("events") or []) if isinstance(per, dict) else []:
                    n = _normalize_event(ev)
                    if n is not None:
                        flat.append(n)
    return flat


async def ai_summarize_outline(
    events: list[dict], router: Any,
    *, prompt_override: str | None = None,
    use_web_search: bool = False,
    work_ctx: dict | None = None,
) -> dict:
    """Step 2 of the outline pipeline: take a flat events list (from
    one or more ``ai_extract_outline_events`` calls) and reorder by
    story-time, grouping into ``epochs[].periods[].events[]``.

    Returns the final chronicle dict ``{"logline", "epochs": [...]}``.
    Events are kept verbatim — the LLM only re-orders and re-groups.
    """
    from analysis.feature_extraction.prompts import render
    if not events:
        return {"logline": "", "epochs": []}
    ctx = _ctx(work_ctx)
    events_json = json.dumps(events, ensure_ascii=False, indent=2)
    prompt = render(
        "reference.outline_summary", override=prompt_override,
        event_count=len(events), events_json=events_json,
        title=ctx["title"], author=ctx["author"],
        volume_index=ctx["volume_index"], volume_title=ctx["volume_title"],
        start_chapter=ctx["start_chapter"], end_chapter=ctx["end_chapter"],
        n_chapters=ctx.get("end_chapter", 0) if isinstance(ctx.get("end_chapter"), int) else 0,
    )
    raw = await _invoke(router, prompt, max_tokens=4096,
                          use_web_search=use_web_search, expect="object")
    obj = _parse_obj(raw)
    epochs_out: list[dict] = []
    for ep in (obj.get("epochs") or []):
        if not isinstance(ep, dict):
            continue
        periods_out: list[dict] = []
        for per in (ep.get("periods") or []):
            if not isinstance(per, dict):
                continue
            events_out = [_normalize_event(ev) for ev in (per.get("events") or [])]
            events_out = [e for e in events_out if e is not None]
            periods_out.append({
                "time": (per.get("time") or "").strip(),
                "events": events_out,
            })
        epochs_out.append({
            "title": (ep.get("title") or "").strip(),
            "periods": periods_out,
        })
    return {
        "logline": (obj.get("logline") or "").strip(),
        "epochs": epochs_out,
    }


async def ai_extract_outline(chapters: list[dict], router: Any,
                                *, prompt_override: str | None = None,
                                use_web_search: bool = False,
                                work_ctx: dict | None = None) -> dict:
    """End-to-end outline extraction for one volume.

    Internally runs the two-step pipeline:
      1) ``ai_extract_outline_events`` — flat per-chapter event list
      2) ``ai_summarize_outline`` — reorder by story-time + group into
         epochs / periods

    Returns ``{"logline", "epochs": [{"title", "periods": [
        {"time", "events": [{
            "subject", "category", "name", "description",
            "time_marker", "first_chapter",
        }]}]}]}``.

    The two-step split is mainly for the manual web-LLM path (where
    each step has its own copy-prompt button), but we apply it
    internally too so the in-process AI sees the same flow and the
    output format stays consistent.
    """
    events = await ai_extract_outline_events(
        chapters, router,
        prompt_override=prompt_override,
        use_web_search=use_web_search,
        work_ctx=work_ctx,
    )
    if not events:
        return {"logline": "", "epochs": []}
    return await ai_summarize_outline(
        events, router,
        use_web_search=use_web_search,
        work_ctx=work_ctx,
    )


async def ai_extract_narrative(chapters: list[dict], router: Any) -> dict:
    """Returns {opening_pattern, climax_positions, hook_density, shuangdian}."""
    text, _ = _build_segment_text(chapters)
    prompt = _NARRATIVE_PROMPT.format(n_chapters=len(chapters), text=text)
    raw = await _invoke(router, prompt, max_tokens=2048, expect="object")
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
    raw = await _invoke(router, prompt, max_tokens=2048, expect="object")
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
                                  use_web_search: bool = False,
                                  work_ctx: dict | None = None) -> dict:
    """Single AI call that produces the consolidated rhythm_json shape
    (replaces ai_extract_narrative + ai_extract_rhythm)."""
    from analysis.feature_extraction.prompts import render
    text, _ = _build_segment_text(chapters)
    prompt = render(
        "reference.rhythm", override=prompt_override,
        n_chapters=len(chapters), text=text,
        **_ctx(work_ctx),
    )
    raw = await _invoke(router, prompt, max_tokens=4096,
                          use_web_search=use_web_search, expect="object")
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
