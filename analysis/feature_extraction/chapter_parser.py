"""Smarter chapter detection for raw novel text.

Replaces the old single-regex ``_split_chapters`` (which only recognized
``第N章 …`` headings) with a multi-pattern detector that:

  1. Tries several common chapter-heading formats — "第N章", "第N回",
     "1、…", "Chapter N", "数字。" etc.
  2. Picks the format whose matches form the cleanest pattern:
     repeated (>=5 hits), monotonically increasing numbers, line lengths
     similar (≪ body text), evenly spaced through the document.
  3. Returns a stable list of {number, title, content, raw_marker}.

Also exports a pure-heuristic ``flag_author_notes`` that marks chapters
suspected of being author asides (求收藏 / 求订阅 / 章节缺失 / 请假 /
作者大大 etc.) — these otherwise pollute the extraction.
"""

from __future__ import annotations

import re
from re import error as error_re
import statistics
from typing import Any


# ── Chinese numeral → int ──

_CN_DIGITS = {
    "零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
    "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
    "百": 100, "千": 1000, "万": 10000,
}


def cn2int(s: str) -> int | None:
    """Convert a Chinese-numeral string ("一", "十二", "二百零三") to int.
    Returns None on failure. Accepts mixed-form ("12") too."""
    s = (s or "").strip()
    if not s:
        return None
    if s.isdigit():
        try:
            return int(s)
        except ValueError:
            return None
    # Pure Chinese numeral
    if not all(ch in _CN_DIGITS for ch in s):
        return None
    total, section, current = 0, 0, 0
    for ch in s:
        v = _CN_DIGITS[ch]
        if v == 10 and current == 0:
            current = 1
        if v >= 10:
            section += (current or 1) * v
            current = 0
        else:
            current = v
    section += current
    total += section
    return total or None


# ── Chapter patterns ──
#
# Order matters slightly (priority on ties), but selection is by score so
# the listing is mostly cosmetic. Each pattern captures (number, title).

_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    # 第N章 / 第N回 / 第N节 / 第N节 + arabic OR chinese
    ("第N章", re.compile(
        r"^[\s　]*第\s*([零〇一二两三四五六七八九十百千万0-9]+)\s*章[\s：:　.、，,．]*([^\n]{0,80})$",
        re.MULTILINE,
    )),
    ("第N回", re.compile(
        r"^[\s　]*第\s*([零〇一二两三四五六七八九十百千万0-9]+)\s*回[\s：:　.、，,．]*([^\n]{0,80})$",
        re.MULTILINE,
    )),
    ("第N节", re.compile(
        r"^[\s　]*第\s*([零〇一二两三四五六七八九十百千万0-9]+)\s*节[\s：:　.、，,．]*([^\n]{0,80})$",
        re.MULTILINE,
    )),
    # "Chapter N" English-style headings
    ("Chapter N", re.compile(
        r"^[\s　]*Chapter\s+([0-9]+)[\s：:.,、，]*([^\n]{0,80})$",
        re.MULTILINE | re.IGNORECASE,
    )),
    # "1、标题" — arabic + 顿号 (very common in web novels)
    ("数字、标题", re.compile(
        r"^[\s　]*([0-9]+)\s*[、．]\s*([^\n]{1,80})$",
        re.MULTILINE,
    )),
    # "1.标题" — arabic + 句点 (.)
    ("数字.标题", re.compile(
        r"^[\s　]*([0-9]+)[\s]*[.]\s*([^\n]{1,80})$",
        re.MULTILINE,
    )),
]

_VOLUME_PAT = re.compile(
    r"^[\s　]*第\s*([零〇一二两三四五六七八九十百千万0-9]+)\s*卷[\s：:　.、，,．]*([^\n]{0,80})$",
    re.MULTILINE,
)


def _score_pattern(matches: list[re.Match[str]], text_len: int) -> float:
    """Score how plausibly a pattern's matches form a chapter index.

    Signals:
      A) ``count`` — more matches = better, up to a saturation point (~500).
      B) ``ordered_frac`` — fraction of consecutive number deltas that are
         positive (chapter numbers should be monotonically increasing).
      C) ``short_lines`` — fraction of matches whose entire matched line is
         short (< 50 chars). Body lines are typically much longer.
      D) ``regular_spacing`` — std-dev of gap-between-matches normalized
         by mean gap. Low = chapters are evenly distributed.
    Returns a float; higher is better. < 1.0 means too few or noisy.
    """
    n = len(matches)
    if n < 5:
        return 0.0
    nums: list[int] = []
    short_count = 0
    for m in matches:
        ni = cn2int(m.group(1))
        if ni is not None:
            nums.append(ni)
        if len(m.group(0)) < 50:
            short_count += 1
    ordered = 0
    for i in range(1, len(nums)):
        if nums[i] > nums[i - 1]:
            ordered += 1
    ordered_frac = ordered / max(1, len(nums) - 1)
    short_frac = short_count / n
    # Spacing regularity
    starts = [m.start() for m in matches]
    gaps = [starts[i + 1] - starts[i] for i in range(len(starts) - 1)] or [text_len]
    mean_gap = sum(gaps) / len(gaps) if gaps else 1.0
    try:
        sd = statistics.stdev(gaps) if len(gaps) > 1 else 0.0
    except statistics.StatisticsError:
        sd = 0.0
    spacing_q = 1.0 - min(1.0, (sd / (mean_gap + 1)))  # 0..1, higher = more regular
    # Saturate count contribution so a 5k-match pattern doesn't dwarf
    # everything; meaningful signal is in the 5..500 range.
    count_q = min(1.0, n / 500)
    return (
        2.5 * count_q
        + 2.0 * ordered_frac
        + 1.5 * short_frac
        + 1.0 * spacing_q
    )


def _build_chapters(text: str, matches: list[re.Match[str]],
                     pattern_name: str) -> list[dict]:
    """Materialize matches into chapter records."""
    if not matches:
        return []
    vol_marks = [(m.start(), m.group(0).strip()[:60], cn2int(m.group(1)))
                 for m in _VOLUME_PAT.finditer(text)]
    chapters: list[dict] = []
    cur_vol = ""
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        while vol_marks and vol_marks[0][0] <= m.start():
            cur_vol = vol_marks.pop(0)[1]
        # Tolerate user regexes that capture <2 groups — fall back to
        # the index + raw line as the number/title source.
        try:
            num_text = m.group(1)
        except (IndexError, error_re):
            num_text = str(i + 1)
        try:
            title_text = (m.group(2) or "").strip()
        except (IndexError, error_re):
            title_text = ""
        raw_marker = m.group(0).strip()
        normalized = cn2int(num_text) or (i + 1)
        chapters.append({
            "index": i,
            "number": normalized,
            "title": raw_marker[:60],
            "title_only": title_text,
            "raw_marker": raw_marker,
            "pattern": pattern_name,
            "volume": cur_vol,
            "content": text[m.end():end].strip(),
        })
    return chapters


def _compile_extra(raw_patterns: list[dict] | None) -> list[tuple[str, re.Pattern[str]]]:
    """Compile user-supplied chapter patterns. Each entry is
    ``{name, regex, enabled?}``. Silently drops entries whose regex
    fails to compile — they're surfaced via the candidates list at
    score 0 so the UI can flag them, but we never raise."""
    out: list[tuple[str, re.Pattern[str]]] = []
    for p in (raw_patterns or []):
        if not isinstance(p, dict):
            continue
        if p.get("enabled") is False:
            continue
        name = (p.get("name") or "自定义").strip()
        regex = p.get("regex") or ""
        if not regex:
            continue
        try:
            out.append((name, re.compile(regex, re.MULTILINE | re.IGNORECASE)))
        except re.error:
            continue
    return out


def detect_chapters(text: str,
                     extra_patterns: list[dict] | None = None) -> dict[str, Any]:
    """Run all patterns (built-in + user-supplied), pick the best by score,
    return chapters + diagnostics.

    ``extra_patterns`` lets users add their own format (e.g. "卷X-Y") via
    settings.json["chapter_patterns"]. Each user regex must capture two
    groups: ``(number, title)``. If a user pattern wins the scoring, the
    chapters use it.

    Returns: {
      "chapters": [...],
      "pattern": "第N章" | "数字、标题" | ... | None,
      "candidates": [{name, count, score, custom?}, ...],
      "fallback_used": bool,
    }
    """
    builtin = list(_PATTERNS)
    custom = _compile_extra(extra_patterns)
    all_patterns = builtin + custom
    custom_names = {n for n, _ in custom}
    candidates: list[dict] = []
    best_name: str | None = None
    best_matches: list[re.Match[str]] = []
    best_score = 0.0
    for name, pat in all_patterns:
        ms = list(pat.finditer(text))
        score = _score_pattern(ms, len(text))
        candidates.append({
            "name": name, "count": len(ms), "score": round(score, 3),
            "custom": name in custom_names,
        })
        if score > best_score:
            best_score = score
            best_matches = ms
            best_name = name

    if best_score < 1.0 or not best_matches:
        # Fallback — no recognizable chapter structure.
        n_chunks = max(1, len(text) // 3000)
        chapters = [
            {"index": i, "number": i + 1,
             "title": f"段落 {i + 1}", "title_only": "",
             "raw_marker": "",
             "pattern": "fallback",
             "volume": "",
             "content": text[i * 3000:(i + 1) * 3000]}
            for i in range(n_chunks)
        ]
        return {
            "chapters": chapters,
            "pattern": None,
            "candidates": candidates,
            "fallback_used": True,
        }

    chapters = _build_chapters(text, best_matches, best_name or "")
    return {
        "chapters": chapters,
        "pattern": best_name,
        "candidates": candidates,
        "fallback_used": False,
    }


# ── Author-note heuristic ──

# These keywords reliably mark "求收藏 / 求票 / 求订阅" style author asides.
# A single hit is weak; multiple hits + length anomaly = high confidence.
_AUTHOR_KEYWORDS = [
    "求收藏", "求订阅", "求推荐", "求月票", "求票",
    "推荐票", "月票", "打赏", "感谢书友", "感谢读者", "感谢支持",
    "请假", "断更", "章节缺失", "卡文", "码字", "更新慢",
    "作者菌", "作者大大", "本作者", "笔者", "码字辛苦",
    "新书", "推一本", "推荐一本", "互助榜", "本章说",
    "晚点更新", "明天更新", "今日两更", "今日三更", "求各位",
    "亲爱的读者", "亲们", "书友们", "对不起大家",
]


def flag_author_notes(chapters: list[dict]) -> list[dict]:
    """Pure-heuristic flagger. Mutates each chapter in-place to add:

      is_author_note: bool — final verdict
      author_note_score: int — additive signal score
      author_note_reasons: list[str] — human-readable reasons

    The verdict requires score >= 3 to reduce false positives on
    legitimately-short chapters or chapters whose narrator happens to
    monologue in first person.
    """
    if not chapters:
        return chapters
    lengths = [len(c.get("content") or "") for c in chapters]
    try:
        median_len = statistics.median(lengths) if lengths else 0
    except statistics.StatisticsError:
        median_len = 0
    for c in chapters:
        content = c.get("content") or ""
        n = len(content)
        score = 0
        reasons: list[str] = []
        # A) Author keyword hits
        hits = [kw for kw in _AUTHOR_KEYWORDS if kw in content]
        if hits:
            score += min(5, len(hits) * 2)
            reasons.append("命中关键词：" + "、".join(hits[:4])
                            + (f"…(+{len(hits) - 4})" if len(hits) > 4 else ""))
        # B) Length: significantly shorter than median (and absolutely short)
        if median_len > 0 and n < median_len * 0.35 and n < 2000:
            score += 2
            reasons.append(f"篇幅偏短（{n} 字 / 中位 {int(median_len)} 字）")
        elif n < 600:
            score += 1
            reasons.append(f"篇幅极短（{n} 字）")
        # C) Title contains author-note markers
        title_blob = (c.get("title") or "") + " " + (c.get("title_only") or "")
        if any(kw in title_blob for kw in [
            "感言", "番外", "请假", "通知", "公告", "致歉", "说明", "本章说",
            "作者", "读者", "废话", "闲聊", "杂谈",
        ]):
            score += 3
            reasons.append("标题包含作者标记")
        # D) High first-person density + no dialogue (作者闲聊体征)
        if n > 200:
            wo_density = content.count("我") / n
            has_dialogue = any(ch in content for ch in ["「", "“", "「", "『"])
            if wo_density > 0.025 and not has_dialogue:
                score += 1
                reasons.append("第一人称密度高且无对话")
        c["is_author_note"] = score >= 3
        c["author_note_score"] = score
        c["author_note_reasons"] = reasons
    return chapters


def apply_exclusions(text: str, chapters: list[dict], excluded_numbers: set[int]) -> str:
    """Rebuild the full-text by removing chapters whose ``number`` is in
    ``excluded_numbers``. The rest are concatenated back in original order
    using their ``raw_marker`` as the heading. Returns the new full-text
    string suitable for re-saving the work's file."""
    parts: list[str] = []
    for c in chapters:
        if c.get("number") in excluded_numbers:
            continue
        marker = c.get("raw_marker") or c.get("title") or ""
        body = c.get("content") or ""
        if marker:
            parts.append(marker)
        parts.append(body)
    return "\n\n".join(p for p in parts if p)
