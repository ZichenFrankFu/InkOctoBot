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
    Returns None on failure. Accepts mixed-form ("12") and full-width
    digits ("１２") too — both common in web-novel uploads."""
    s = (s or "").strip()
    if not s:
        return None
    # Normalize full-width digits to ASCII so str.isdigit / int parse.
    fw_to_ascii = str.maketrans("０１２３４５６７８９", "0123456789")
    s = s.translate(fw_to_ascii)
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
    # "1、标题" — arabic + 顿号 (very common in web novels).
    # Title is optional ({0,80}) so bare "147、" lines also match —
    # otherwise the chapter gets absorbed into the previous one and
    # content lengths explode (e.g. a 2800-字 chapter shows as 387.6
    # 万字 because it ate the rest of the document). Includes
    # full-width digits ０-９ since web-novel uploads sometimes use them.
    ("数字、标题", re.compile(
        r"^[\s　]*([0-9０-９]+)\s*[、．][\s　]*([^\n]{0,80})$",
        re.MULTILINE,
    )),
    # "1.标题" — arabic + 句点 (.)
    ("数字.标题", re.compile(
        r"^[\s　]*([0-9０-９]+)[\s]*[.][\s　]*([^\n]{0,80})$",
        re.MULTILINE,
    )),
    # No-numbering format: short paragraphs (1–26 chars) that don't end
    # in terminal punctuation and stand alone between blank lines.
    # Captures the WHOLE title as group(1); _build_chapters detects this
    # case (cn2int(group(1)) returns None) and synthesizes 1, 2, 3…
    # numbers in document order. Common in literary works that title
    # chapters with a phrase but no chapter number.
    ("末尾无标点", re.compile(
        r"(?:^|\n[\s　]*\n)[\s　]*"
        r"([^\s。！？；，,.!?…：:　][^\n。！？；，,.!?…：:]{0,24}[^\s。！？；，,.!?…：:　])"
        r"[\s　]*(?=\n[\s　]*\n)",
        re.MULTILINE,
    )),
    # Author-aside "chapters" — paragraph-isolated lines that contain
    # WHOLE-CHAPTER author markers (上架感言 / 老书友请进 / 请假说明 …).
    # Deliberately STRICTER than _AUTHOR_KEYWORDS: only keywords that
    # signal "this entire paragraph is an author chapter," not weak
    # markers like 求月票 that show up as PS lines inside real chapters
    # (those are handled by detect_aside_paragraphs / paragraph cleanup).
    ("作者说章节", re.compile(
        r"(?:^|\n[\s　]*\n)[\s　]*"
        r"([^\n]{0,80}?"
        r"(?:上架感言|完本感言|请假说明|断更通知|新书发布|"
        r"本书公告|作者公告|作者的话|作者说明|作者寄语|"
        r"加更说明|更新说明|双更说明|明天恢复|"
        r"老书友请进|书友通知|书友们请看|"
        r"作者菌|作者大大)"
        r"[^\n]{0,80})[\s　]*(?=\n[\s　]*\n)",
        re.MULTILINE,
    )),
]

_VOLUME_PAT = re.compile(
    r"^[\s　]*第\s*([零〇一二两三四五六七八九十百千万0-9]+)\s*卷[\s：:　.、，,．]*([^\n]{0,80})$",
    re.MULTILINE,
)


def visible_char_count(s: str) -> int:
    """Count meaningful characters: everything except whitespace (\\s).
    Matches the standard Word-style 字数 convention — each CJK char,
    each ASCII letter/digit, each punctuation mark counts; spaces /
    tabs / newlines / full-width spaces do not."""
    if not s:
        return 0
    return sum(1 for c in s if not c.isspace() and c != "　")


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
    if not nums:
        # Pattern captures a title (not a number) — synthesized 1, 2, 3 …
        # numbering is always monotonic, so ordered_frac is trivially 1.0.
        # We penalize slightly via a 0.9 multiplier so a real numbered
        # pattern with the same match count wins over an unnumbered one
        # (avoids accidental hits in literary works that also have
        # 第N章 markers).
        ordered_frac = 0.9
    else:
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


# Strip parenthesized noise from chapter titles — e.g. "第一章 邂逅（求月票）"
# becomes "第一章 邂逅". Matches both full-width and half-width brackets.
_PAREN_NOISE = re.compile(r"[（(]\s*[^（()）]*\s*[)）]")


def _clean_title(s: str) -> str:
    """Remove parenthesized asides like '（求月票）' / '(求收藏)' from chapter
    titles, collapse whitespace, and trim."""
    if not s:
        return s
    out = _PAREN_NOISE.sub("", s)
    out = re.sub(r"\s+", " ", out).strip()
    return out


_UNNUMBERED_PATTERNS = {"末尾无标点", "作者说章节"}


def _build_chapters(text: str,
                     named_matches: list[tuple[str, re.Match[str]]]) -> list[dict]:
    """Materialize a merged list of (pattern_name, match) pairs into
    chapter records.

    Accepts mixed patterns so the primary numbered pattern (e.g.
    第N章) and secondary patterns (作者说章节, custom user formats) can
    coexist in one chapter list. Matches must already be sorted by
    ``match.start()``.

    Deduplication strategy: when a chapter number appears more than once
    (TOC + body, or repeated mentions), keep the occurrence whose
    *content gap to the next match* is longest. That's almost always
    the real body chapter — TOC entries are tightly packed and have no
    body after them. Adaptive short-gap dedup also catches standalone
    TOC entries.
    """
    if not named_matches:
        return []

    text_len = len(text)
    matches = [m for _, m in named_matches]

    def title_start(m: re.Match[str]) -> int:
        """Position where the chapter heading effectively starts.

        Splits on the nature of the pattern's prefix (the chars between
        match start and group(1)):
          - **Whitespace only** (``(?:^|\\n\\n)``-style): the prefix is
            cosmetic — return group(1) start (the title proper).
          - **Content** (``第`` / ``Chapter``): the prefix IS part of
            the heading — return the trimmed full-match start so the
            slice retains the heading prefix.
        """
        s0 = m.start()
        try:
            s1 = m.start(1)
        except (IndexError, error_re):
            return s0
        if s1 == s0:
            return s0
        prefix = m.string[s0:s1]
        if prefix.strip() == "":
            return s1
        leading_ws = len(prefix) - len(prefix.lstrip())
        return s0 + leading_ws

    def gap_to_next(i: int) -> int:
        nxt = title_start(matches[i + 1]) if i + 1 < len(matches) else text_len
        return max(0, nxt - matches[i].end())

    def is_unnumbered_at(i: int) -> bool:
        return named_matches[i][0] in _UNNUMBERED_PATTERNS

    # Number-based dedup only applies to NUMBERED matches (unnumbered
    # patterns synthesize indices, so collisions are meaningless).
    by_num: dict[int, list[int]] = {}
    for i, m in enumerate(matches):
        if is_unnumbered_at(i):
            continue
        n = cn2int(m.group(1))
        if n is None:
            continue
        by_num.setdefault(n, []).append(i)

    drop_idx: set[int] = set()
    for n, idxs in by_num.items():
        if len(idxs) < 2:
            continue
        best = max(idxs, key=gap_to_next)
        for j in idxs:
            if j != best:
                drop_idx.add(j)

    # Adaptive short-gap dedup — applied ONLY to numbered matches.
    # Secondary patterns (作者说章节, 末尾无标点, custom) can legitimately
    # cluster close together (author asides between two body chapters),
    # so excluding them from the gap calculation prevents the dedup
    # from sweeping them out as TOC noise.
    surviving = [i for i in range(len(matches))
                 if i not in drop_idx and not is_unnumbered_at(i)]
    if len(surviving) >= 4:
        gaps = sorted(gap_to_next(i) for i in surviving)
        median_gap = gaps[len(gaps) // 2]
        threshold = min(80, max(0, median_gap * 0.25))
        for i in surviving:
            if gap_to_next(i) < threshold:
                drop_idx.add(i)

    pruned_pairs = [named_matches[i] for i in range(len(named_matches)) if i not in drop_idx]
    if not pruned_pairs:
        pruned_pairs = named_matches[len(named_matches) // 2:]
    if not pruned_pairs:
        pruned_pairs = list(named_matches)

    vol_marks = [(m.start(), m.group(0).strip()[:60], cn2int(m.group(1)))
                 for m in _VOLUME_PAT.finditer(text)]
    chapters: list[dict] = []
    cur_vol = ""
    pruned_matches = [m for _, m in pruned_pairs]
    for i, (pname, m) in enumerate(pruned_pairs):
        end = title_start(pruned_matches[i + 1]) if i + 1 < len(pruned_matches) else text_len
        while vol_marks and vol_marks[0][0] <= m.start():
            cur_vol = vol_marks.pop(0)[1]
        is_unnumbered = pname in _UNNUMBERED_PATTERNS
        try:
            g1 = m.group(1)
        except (IndexError, error_re):
            g1 = ""
        if is_unnumbered:
            num_text = ""
            title_text = (g1 or "").strip().rstrip("\r")
        else:
            num_text = g1 or ""
            try:
                title_text = (m.group(2) or "").strip().rstrip("\r")
            except (IndexError, error_re):
                title_text = ""
        title_text = _clean_title(title_text)
        raw_marker = _clean_title(m.group(0).strip().rstrip("\r"))
        normalized = cn2int(num_text) or (i + 1)
        content_start = m.end()
        content_end = end
        content = text[content_start:content_end].strip()
        display_title = title_text if is_unnumbered else raw_marker[:60]
        chapters.append({
            "number": normalized,
            "title": display_title,
            "title_only": title_text,
            "raw_marker": raw_marker,
            "pattern": pname,
            "volume": cur_vol,
            "content": content,
            # CHAR offsets into the source text — let the chapter-edit
            # endpoint slice the file directly instead of re-running
            # detect_chapters on the full text.
            "content_start": content_start,
            "content_end": content_end,
        })

    # Chapters are already in document order. Final dedup: only collapse
    # adjacent NUMBERED chapters with the same parsed number (TOC + body
    # collision). Unnumbered chapters never collapse.
    deduped: list[dict] = []
    for c in chapters:
        if (
            deduped
            and c["pattern"] not in _UNNUMBERED_PATTERNS
            and deduped[-1]["pattern"] == c["pattern"]
            and deduped[-1]["number"] == c["number"]
        ):
            if len(c["content"]) > len(deduped[-1]["content"]):
                deduped[-1] = c
            continue
        deduped.append(c)
    # Rescue split: any chapter whose content is grossly larger than
    # the median is almost certainly hiding a missed heading. Re-scan
    # its content with the same pattern that built it, and split at any
    # internal matches. Catches the "chapter 147 shows 387.6 万字"
    # symptom even when the boundary missed for non-formatting reasons
    # (full-width digits, stray whitespace, unrecognized variant).
    deduped = _rescue_split_huge_chapters(text, deduped)

    # Preserve each numbered chapter's parsed value as ``parsed_number``
    # (for display via the title), and re-assign ``number`` to a clean
    # 1, 2, 3... ordinal so the UI list is monotone in reading order
    # regardless of mixed numbered + unnumbered chapters.
    for i, c in enumerate(deduped):
        c["index"] = i
        c["parsed_number"] = c["number"] if c["pattern"] not in _UNNUMBERED_PATTERNS else None
        c["number"] = i + 1

    return deduped


def _rescue_split_huge_chapters(text: str, chapters: list[dict]) -> list[dict]:
    """Post-process: split any chapter whose content is > 4× median
    chapter length (and > 30k chars absolute) at internal heading
    matches. The internal scan re-runs ALL built-in patterns over the
    chapter's content; matches re-establish the chapter boundaries
    that the initial pass missed."""
    if len(chapters) < 3:
        return chapters
    lengths = [len(c.get("content") or "") for c in chapters]
    try:
        median = statistics.median(lengths)
    except statistics.StatisticsError:
        return chapters
    threshold = max(median * 4 if median else 0, 30000)
    out: list[dict] = []
    for c in chapters:
        content = c.get("content") or ""
        if len(content) <= threshold:
            out.append(c)
            continue
        # Scan content with all numbered built-in patterns for any
        # heading-shaped lines that should have split this chapter.
        # Skip the no-number patterns since they'd over-split body
        # text on random short lines.
        rescue_matches: list[tuple[str, "re.Match[str]"]] = []
        for name, pat in _PATTERNS:
            if name in _UNNUMBERED_PATTERNS:
                continue
            for m in pat.finditer(content):
                # Drop the very-first match if it starts at content[0] —
                # that's just this chapter's own heading being re-detected.
                if m.start() < 4:
                    continue
                rescue_matches.append((name, m))
        if not rescue_matches:
            out.append(c)
            continue
        rescue_matches.sort(key=lambda x: x[1].start())
        base_offset = c.get("content_start") or 0
        cursor = 0
        last_marker = c.get("raw_marker") or c.get("title") or ""
        for name, rm in rescue_matches:
            # First piece keeps the original chapter's marker, subsequent
            # pieces use the rescued heading as their marker.
            sub_content = content[cursor:rm.start()].strip()
            out.append({
                **c,
                "content": sub_content,
                "raw_marker": last_marker,
                "content_start": base_offset + cursor,
                "content_end": base_offset + rm.start(),
            })
            last_marker = _clean_title(rm.group(0).strip().rstrip("\r"))
            cursor = rm.end()
        # Final piece
        out.append({
            **c,
            "content": content[cursor:].strip(),
            "raw_marker": last_marker,
            "title": last_marker[:60],
            "content_start": base_offset + cursor,
            "content_end": base_offset + len(content),
        })
    return out


# ── Format-string → regex translation ──
#
# Users type a literal heading template like "第N章" or "N、" or "卷N" and
# we generate a regex that captures the number + optional title. ``N`` is
# the placeholder for the chapter number — it expands to a group matching
# either Arabic digits OR Chinese numerals.

_NUMBER_GROUP = r"([零〇一二两三四五六七八九十百千万0-9]+)"
_TITLE_GROUP = r"([^\n]{0,80})"
# Special regex chars we must escape when treating the user's input as a literal.
_RE_META = set(r".^$*+?{}[]\|()")


def format_to_regex(fmt: str) -> str:
    """Convert a user-friendly heading template into a regex pattern.

    Rules:
      - ``N`` is the chapter-number placeholder; it expands to a digit /
        Chinese-numeral capture group.
      - All other characters are escaped (treated as literal text).
      - The regex is anchored to the start of a line, allows optional
        leading whitespace, and captures an optional title after the
        heading (separated by colons / dots / commas / 顿号 / spaces).

    Examples:
      ``第N章``     → matches "第1章", "第一章", "第 12 章 …"
      ``第N回``     → matches "第N回" style
      ``N、``       → matches "1、章节标题"
      ``N.``        → matches "1. 章节标题"
      ``卷N``       → matches "卷1", "卷一二三"
      ``Chapter N`` → matches "Chapter 12: Title"
    """
    fmt = (fmt or "").strip()
    if not fmt:
        return ""
    parts = []
    n_count = 0
    i = 0
    while i < len(fmt):
        ch = fmt[i]
        if ch == "N":
            parts.append(_NUMBER_GROUP)
            n_count += 1
            i += 1
        elif ch.isspace() or ch == "　":
            # Any whitespace in the template is forgiving — match any
            # mix of spaces / tabs / full-width spaces.
            parts.append(r"[\s　]*")
            i += 1
        else:
            if ch in _RE_META:
                parts.append("\\" + ch)
            else:
                parts.append(ch)
            i += 1
    if n_count == 0:
        # No number placeholder — likely a literal heading. Doesn't make
        # sense as a chapter marker, but compile anyway with no number
        # group to avoid a confusing error.
        body = "".join(parts)
    else:
        body = "".join(parts)
    return rf"^[\s　]*{body}[\s：:　.、，,．]*{_TITLE_GROUP}$"


def _compile_extra(raw_patterns: list[dict] | None) -> list[tuple[str, re.Pattern[str]]]:
    """Compile user-supplied chapter patterns. Each entry is
    ``{name, format?|regex?, enabled?}``. Prefers ``format`` (user-friendly
    template, e.g. "第N章") and falls back to raw ``regex`` for backward
    compat. Silently drops entries that fail to compile."""
    out: list[tuple[str, re.Pattern[str]]] = []
    for p in (raw_patterns or []):
        if not isinstance(p, dict):
            continue
        if p.get("enabled") is False:
            continue
        name = (p.get("name") or "自定义").strip()
        fmt = (p.get("format") or "").strip()
        regex = (p.get("regex") or "").strip()
        if fmt and not regex:
            regex = format_to_regex(fmt)
        if not regex:
            continue
        try:
            out.append((name, re.compile(regex, re.MULTILINE | re.IGNORECASE)))
        except re.error:
            continue
    return out


def detect_chapters(text: str,
                     extra_patterns: list[dict] | None = None,
                     force_pattern: str | None = None,
                     force_patterns: list[str] | None = None) -> dict[str, Any]:
    """Run all patterns, pick the best by score (or honor the user's
    explicit pick), return chapters + diagnostics.

    Pattern selection priority:
      1. ``force_patterns`` (multi-select) — use EXACTLY these patterns,
         merging their matches in document order. No secondary auto-merge.
         Empty list ⇒ falls back to auto-pick.
      2. ``force_pattern`` (single, legacy) — sets primary, secondary
         patterns still auto-merged.
      3. No force args — auto-score, pick primary, auto-merge secondaries.
    """
    builtin = list(_PATTERNS)
    custom = _compile_extra(extra_patterns)
    all_patterns = builtin + custom
    custom_names = {n for n, _ in custom}
    candidates: list[dict] = []
    best_name: str | None = None
    best_matches: list[re.Match[str]] = []
    best_score = 0.0
    forced_hit = False

    # Need this helper for the multi-select branch below
    def title_bounds(m: re.Match[str]) -> tuple[int, int]:
        try:
            return m.start(1), m.end(1)
        except (IndexError, re.error):
            return m.start(), m.end()

    # ── Multi-select fast path ──
    chosen_set = set(p for p in (force_patterns or []) if p)
    if chosen_set:
        named_matches: list[tuple[str, re.Match[str]]] = []
        for name, pat in all_patterns:
            if name not in chosen_set:
                # Skip the regex scan entirely for non-chosen patterns —
                # surface in candidates with count=-1 so the UI can show
                # "(未扫描)" instead of a misleading 0. This is the main
                # optimization for big works: scanning every built-in
                # pattern against a 10 MB text used to take seconds.
                candidates.append({
                    "name": name, "count": -1, "score": 0.0,
                    "custom": name in custom_names,
                })
                continue
            ms = list(pat.finditer(text))
            candidates.append({
                "name": name, "count": len(ms), "score": 0.0,
                "custom": name in custom_names,
            })
            for m in ms:
                named_matches.append((name, m))
        named_matches.sort(key=lambda x: title_bounds(x[1])[0])
        # Drop overlapping matches (e.g., two formats hitting the same heading)
        deduped: list[tuple[str, re.Match[str]]] = []
        last_end = -1
        for name, m in named_matches:
            s, e = title_bounds(m)
            if s < last_end:
                continue
            deduped.append((name, m))
            last_end = e
        chapters = _build_chapters(text, deduped)
        contributing = sorted({c["pattern"] for c in chapters if c.get("pattern")})
        return {
            "chapters": chapters,
            "pattern": next(iter(chosen_set)) if len(chosen_set) == 1 else None,
            "merged_patterns": contributing,
            "candidates": candidates,
            "fallback_used": False,
        }

    for name, pat in all_patterns:
        ms = list(pat.finditer(text))
        score = _score_pattern(ms, len(text))
        candidates.append({
            "name": name, "count": len(ms), "score": round(score, 3),
            "custom": name in custom_names,
        })
        if force_pattern and name == force_pattern and ms:
            # Honor the user's explicit pick (skip scoring tiebreak).
            best_score = max(score, 1.0)
            best_matches = ms
            best_name = name
            forced_hit = True
        elif not forced_hit and score > best_score:
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
            "merged_patterns": [],
            "candidates": candidates,
            "fallback_used": True,
        }

    # Merge primary winner with secondary patterns:
    #   - all custom user patterns (always merged — that's the point of
    #     adding them)
    #   - the built-in 作者说章节 pattern (always merged — author asides
    #     don't compete for primary, they augment)
    # Sort by position, drop overlapping secondary matches that overlap
    # a primary match (within 50 chars to absorb whitespace differences).
    primary_named: list[tuple[str, re.Match[str]]] = [
        (best_name or "", m) for m in best_matches
    ]
    secondary_named: list[tuple[str, re.Match[str]]] = []
    for name, pat in all_patterns:
        if name == best_name:
            continue
        if name not in custom_names and name != "作者说章节":
            continue
        for m in pat.finditer(text):
            secondary_named.append((name, m))

    # Use TITLE bounds (group 1) for overlap math — secondary patterns
    # that consume \n\n prefixes have misleading whole-match ranges.
    primary_title_ranges = [title_bounds(m) for m in best_matches]
    def overlaps_primary(m: re.Match[str]) -> bool:
        s, e = title_bounds(m)
        for ps, pe in primary_title_ranges:
            if s < pe and e > ps:
                return True
        return False
    secondary_named = [(n, m) for n, m in secondary_named if not overlaps_primary(m)]

    merged_named = primary_named + secondary_named
    merged_named.sort(key=lambda x: title_bounds(x[1])[0])
    # Drop any remaining overlaps (titles starting inside a previous
    # match's title range).
    deduped_named: list[tuple[str, re.Match[str]]] = []
    last_end = -1
    for name, m in merged_named:
        s, e = title_bounds(m)
        if s < last_end:
            continue
        deduped_named.append((name, m))
        last_end = e

    chapters = _build_chapters(text, deduped_named)
    # Which patterns actually contributed at least one chapter
    contributing = sorted({c["pattern"] for c in chapters if c.get("pattern")})
    return {
        "chapters": chapters,
        "pattern": best_name,
        "merged_patterns": contributing,
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
    # Sponsor / 盟主 markers — only the unambiguous ones. Removed
    # 舵主/堂主/护法/长老/执事 since those are common character titles
    # in cultivation / wuxia novels and triggered too many false
    # positives (a chapter mentioning "长老说道:" got flagged).
    "盟主", "白银盟", "感谢盟主", "感谢打赏", "万赏", "万订", "月票榜", "豪掷",
]


_TITLE_AUTHOR_MARKERS = [
    "感言", "请假", "通知", "公告", "致歉", "本章说",
    "作者菌", "作者大大", "废话", "闲聊", "杂谈",
    "上架感言", "完本感言", "新书发布",
]


def get_effective_author_keywords(extra: list[str] | None = None) -> list[str]:
    """Return the keyword list used for author-note detection. If the
    caller passes a non-empty ``extra`` list (typically loaded from
    settings.json), it REPLACES the built-in defaults — letting the
    user fully manage the keyword set via the UI."""
    if isinstance(extra, list):
        cleaned = [str(k).strip() for k in extra if k and str(k).strip()]
        if cleaned:
            return cleaned
    return list(_AUTHOR_KEYWORDS)


def flag_author_notes(chapters: list[dict],
                       extra_keywords: list[str] | None = None) -> list[dict]:
    """Heuristic flagger. Mutates each chapter in-place to add:

      is_author_note: bool — final verdict
      author_note_score: int — additive signal score
      author_note_reasons: list[str] — human-readable reasons

    Decision rule (CONSERVATIVE to avoid false positives on normal short
    chapters): require a "strong" signal — keyword in body OR title
    marker — AND at least one supporting signal (length anomaly or
    first-person density). Length alone is NEVER enough.
    """
    if not chapters:
        return chapters
    keywords = get_effective_author_keywords(extra_keywords)
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
        has_strong_signal = False

        # 0) If this chapter was detected by the 作者说章节 pattern, it's
        # an author aside by construction — auto-flag with high score.
        if c.get("pattern") == "作者说章节":
            has_strong_signal = True
            score += 6
            reasons.append("由「作者说章节」格式识别")

        # A) Author keyword hits in BOTH title and content (STRONG signal)
        haystack = content + " " + (c.get("title") or "")
        hits = [kw for kw in keywords if kw in haystack]
        if hits:
            has_strong_signal = True
            score += min(5, len(hits) * 2)
            reasons.append("命中关键词：" + "、".join(hits[:4])
                            + (f"…(+{len(hits) - 4})" if len(hits) > 4 else ""))

        # B) Title author-marker (STRONG signal) — but only specific markers
        # that almost always mean author asides ("感言"/"请假"/"上架感言" etc.).
        # Removed generic markers like "作者"/"读者" since they appear in many
        # real chapter titles (e.g. "作者归来" being part of the plot).
        title_blob = (c.get("title") or "") + " " + (c.get("title_only") or "")
        if any(kw in title_blob for kw in _TITLE_AUTHOR_MARKERS):
            has_strong_signal = True
            score += 4
            reasons.append("标题包含作者标记")

        # C) Length anomaly (SUPPORTING). User reports: real chapters
        # 2000-5000 字, author notes ≤ 1000 字. Use absolute thresholds
        # plus a relative one against the median.
        if n < 1000 and (median_len == 0 or n < median_len * 0.4):
            score += 2
            reasons.append(f"篇幅偏短（{n} 字 / 中位 {int(median_len)} 字）")
        elif n < 1500 and median_len > 0 and n < median_len * 0.5:
            score += 1
            reasons.append(f"篇幅略短（{n} 字 / 中位 {int(median_len)} 字）")

        # D) First-person density + no dialogue (SUPPORTING)
        if n > 200:
            wo_density = content.count("我") / n
            has_dialogue = any(ch in content for ch in ["「", "“", "『"])
            if wo_density > 0.03 and not has_dialogue:
                score += 1
                reasons.append("第一人称密度高且无对话")

        # Final decision: must have STRONG signal AND score >= 4.
        c["is_author_note"] = has_strong_signal and score >= 4
        c["author_note_score"] = score
        c["author_note_reasons"] = reasons
    return chapters


def flag_length_outliers(chapters: list[dict]) -> list[dict]:
    """Mark chapters whose content length is unusually short or long
    relative to the median. Sets ``is_length_outlier: bool`` +
    ``outlier_kind: "短" | "长" | None``. Lets the UI flag chapters
    whose detected boundary may be wrong (e.g. a TOC entry that slipped
    past dedup, or two adjacent chapters merged because a heading was
    missed)."""
    if not chapters:
        return chapters
    lengths = [len(c.get("content") or "") for c in chapters]
    try:
        median = statistics.median(lengths) if lengths else 0
    except statistics.StatisticsError:
        median = 0
    for c in chapters:
        n = len(c.get("content") or "")
        kind: str | None = None
        if median > 0:
            if n < median * 0.3:
                kind = "短"
            elif n > median * 3.0:
                kind = "长"
        c["is_length_outlier"] = kind is not None
        c["outlier_kind"] = kind
    return chapters


def make_preview(content: str, head_chars: int = 180, tail_chars: int = 140) -> dict:
    """Return ``{head, tail}`` previews. Picks at sentence boundary where
    possible so the preview reads cleanly.

    Avoids ``content.strip()`` on huge content — for a 4M-char chapter
    (which signals a missed boundary anyway) the strip alone took
    hundreds of ms and contributed to the post-detection freeze. We
    only need to trim whitespace off the actual head + tail slices."""
    if not content:
        return {"head": "", "tail": ""}
    n = len(content)
    head = content[:head_chars].lstrip()
    for sep in ["。", "！", "？", "”", "」", "\n"]:
        idx = head.rfind(sep)
        if idx >= head_chars * 0.5:
            head = head[: idx + 1]
            break
    if n <= head_chars + tail_chars + 20:
        return {"head": head.strip(), "tail": ""}
    tail = content[-tail_chars:].rstrip()
    for sep in ["。", "！", "？", "”", "」", "\n"]:
        idx = tail.find(sep)
        if 0 <= idx <= tail_chars * 0.5:
            tail = tail[idx + 1:]
            break
    return {"head": head.strip(), "tail": tail.strip()}


def replace_chapter_content(text: str, chapters: list[dict],
                              chapter_number: int, new_content: str) -> str:
    """Return a new full-text where chapter ``chapter_number``'s body has
    been swapped for ``new_content``. Reassembles by concatenating every
    chapter's heading + body in order — same shape as ``apply_exclusions``
    so the on-disk file format stays consistent across edits."""
    parts: list[str] = []
    found = False
    for c in chapters:
        marker = c.get("raw_marker") or c.get("title") or ""
        if c.get("number") == chapter_number:
            body = (new_content or "").strip()
            found = True
        else:
            body = c.get("content") or ""
        if marker:
            parts.append(marker)
        parts.append(body)
    if not found:
        raise ValueError(f"chapter {chapter_number} not found")
    return "\n\n".join(p for p in parts if p)


def detect_aside_paragraphs(chapters: list[dict],
                              max_para_chars: int = 500,
                              extra_keywords: list[str] | None = None) -> list[dict]:
    """For every regular (non-作者说章节) chapter, find PARAGRAPHS that
    look like author asides — short blocks containing author-note
    keywords (求月票 / 求订阅 / 老书友 / 感谢 / 盟主 / …) at any position
    in the chapter. These are the trailing "求月票！" pleas embedded
    at the end of body chapters that the user wants removed without
    losing the chapter itself.

    Returns: list of {chapter_number, chapter_title, para_index,
    para_total, text, reasons, score}. The ``text`` field is the
    authoritative identifier for removal — apply_aside_paragraph_cleanup
    matches on it so minor detection drift between calls doesn't
    misalign indices.
    """
    keywords = get_effective_author_keywords(extra_keywords)
    out: list[dict] = []
    for c in chapters:
        if c.get("pattern") == "作者说章节":
            continue  # whole-chapter asides — handled by the other button
        content = c.get("content") or ""
        if not content:
            continue
        paragraphs = re.split(r"\n\s*\n", content)
        n_paras = len(paragraphs)
        for pi, para in enumerate(paragraphs):
            ps = para.strip()
            if not ps:
                continue
            # Skip long paragraphs — narrative, not asides.
            if len(ps) > max_para_chars:
                continue
            hits = [kw for kw in keywords if kw in ps]
            if not hits:
                continue
            reasons = ["命中：" + "、".join(hits[:3])
                        + (f"…(+{len(hits) - 3})" if len(hits) > 3 else "")]
            is_at_end = pi >= n_paras - 2
            if is_at_end:
                reasons.append("位于章节末尾")
            score = len(hits) + (2 if is_at_end else 0) + (1 if len(ps) < 100 else 0)
            out.append({
                "chapter_number": c["number"],
                "chapter_title": c["title"],
                "para_index": pi,
                "para_total": n_paras,
                "text": ps,
                "reasons": reasons,
                "score": score,
            })
    return out


def apply_aside_paragraph_cleanup(text: str, chapters: list[dict],
                                    paragraphs_to_remove: list[dict]) -> str:
    """Remove the specified paragraphs from their chapters and rebuild
    the full text. Each entry in ``paragraphs_to_remove`` should have
    ``chapter_number`` + ``para_index`` AND ``text`` — we match by
    text content first (robust against minor detection drift between
    the modal-fetch call and the apply call) and fall back to index.
    Chapters not touched keep their original raw_marker + content
    concatenation.
    """
    by_chapter_text: dict[int, set[str]] = {}
    by_chapter_idx: dict[int, set[int]] = {}
    for p in paragraphs_to_remove:
        try:
            n = int(p.get("chapter_number"))
        except (TypeError, ValueError):
            continue
        t = (p.get("text") or "").strip()
        if t:
            by_chapter_text.setdefault(n, set()).add(t)
        try:
            i = int(p.get("para_index"))
            by_chapter_idx.setdefault(n, set()).add(i)
        except (TypeError, ValueError):
            pass

    parts: list[str] = []
    removed = 0
    for c in chapters:
        marker = c.get("raw_marker") or c.get("title") or ""
        body = c.get("content") or ""
        n = c.get("number")
        if body and (n in by_chapter_text or n in by_chapter_idx):
            paragraphs = re.split(r"\n\s*\n", body)
            target_texts = by_chapter_text.get(n, set())
            target_idxs = by_chapter_idx.get(n, set())
            kept: list[str] = []
            for pi, p in enumerate(paragraphs):
                ps = p.strip()
                if not ps:
                    continue
                # Prefer text match; fall back to index when text didn't match.
                if ps in target_texts:
                    removed += 1
                    continue
                if pi in target_idxs and not target_texts:
                    removed += 1
                    continue
                kept.append(ps)
            body = "\n\n".join(kept)
        if marker:
            parts.append(marker)
        if body:
            parts.append(body)
    return "\n\n".join(p for p in parts if p)


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
