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
    # 第N章 / 第N回 / 第N节 / Chapter N — title is OPTIONAL. When a title
    # is present it must NOT end with terminal punctuation; trailing
    # whitespace before \n is tolerated. CRLF endings are handled by
    # normalizing \r\n → \n in detect_chapters BEFORE these run.
    #
    # N、 and N. require a non-empty title (bare "147、" countdowns
    # should not match) — that's enforced below in those patterns only.
    ("第N章", re.compile(
        r"^[\s　]*第\s*([零〇一二两三四五六七八九十百千万0-9０-９]+)\s*章"
        r"(?:[\s：:　.、，,．]+([^\n]{0,79}?[^\s。，,.；;：:？?]))?"
        r"[\s　]*$",
        re.MULTILINE,
    )),
    ("第N回", re.compile(
        r"^[\s　]*第\s*([零〇一二两三四五六七八九十百千万0-9０-９]+)\s*回"
        r"(?:[\s：:　.、，,．]+([^\n]{0,79}?[^\s。，,.；;：:？?]))?"
        r"[\s　]*$",
        re.MULTILINE,
    )),
    ("第N节", re.compile(
        r"^[\s　]*第\s*([零〇一二两三四五六七八九十百千万0-9０-９]+)\s*节"
        r"(?:[\s：:　.、，,．]+([^\n]{0,79}?[^\s。，,.；;：:？?]))?"
        r"[\s　]*$",
        re.MULTILINE,
    )),
    # "Chapter N" English-style headings
    ("Chapter N", re.compile(
        r"^[\s　]*Chapter\s+([0-9]+)"
        r"(?:[\s：:.,、，]+([^\n]{0,79}?[^\s。，,.；;：:？?]))?"
        r"[\s　]*$",
        re.MULTILINE | re.IGNORECASE,
    )),
    # "1、标题" — arabic + 顿号 (very common in web novels).
    # REQUIRES a non-empty title (1+ char, non-terminal-punct end) so
    # bare "147、" lines + countdowns like "10、9、8、" don't match.
    # Full-width digits ０-９ supported. Trailing whitespace OK.
    # Title MUST NOT start with another digit — keeps "1.1 XXX"-style
    # section numbers from being misread as N、 chapter 1 (the title
    # would be "1 XXX", a clear section-number tell).
    ("N、", re.compile(
        r"^[\s　]*([0-9０-９]+)\s*[、][\s　]*"
        r"(?![0-9０-９])"
        r"([^\n]{0,79}?[^\s。，,.；;：:？?])[\s　]*$",
        re.MULTILINE,
    )),
    # "1.标题" — arabic + 句点 (both regular `.` and full-width `．`).
    # Same title-required + no-leading-digit rule as N、 so that
    # "1.1 作者闲谈" / "1.2.3 …" / "２．１ XXX" don't masquerade as
    # chapter 1 (they're section numbers, not chapter headings).
    ("N.", re.compile(
        r"^[\s　]*([0-9０-９]+)[\s]*[.．][\s　]*"
        r"(?![0-9０-９])"
        r"([^\n]{0,79}?[^\s。，,.；;：:？?])[\s　]*$",
        re.MULTILINE,
    )),
    # Short standalone heading lines without terminal punctuation
    # (literary works that title chapters with a phrase but no number).
    # Title cap shrunk to 14 chars to make this a "short sentence"
    # filter. Captures the whole title as group(1); _build_chapters
    # detects this case (cn2int(group(1)) returns None) and synthesizes
    # 1, 2, 3… numbers in document order.
    #
    # Negative lookaheads enforce MUTUAL EXCLUSION with the numbered
    # 缩进对比: title line has NO leading indent + ends without
    # terminal punctuation + the NEXT line begins with whitespace
    # (typical web-novel layout: chapter title at column 0, body
    # lines indented by 2 spaces / one 全角空格). Replaces the old
    # 短句标题 fallback which was over-matching short narrative
    # paragraphs.
    #
    # Negative lookaheads keep this format mutually exclusive with
    # the numbered patterns above — a line that's already "N、" /
    # "第N章" / "Chapter N" won't double-match here.
    ("缩进对比", re.compile(
        r"(?:^|\n)"
        r"(?![0-9０-９]+[\s　]*[、．])"
        r"(?!第\s*[零〇一二两三四五六七八九十百千万0-9０-９]+\s*[章回节卷])"
        r"(?!Chapter[\s　]+[0-9])"
        r"([^\s。！？；，,.!?…：:　][^\n。！？；，,.!?…：:]{0,40}[^\s。！？；，,.!?…：:　])"
        # Next line must start with REAL indent (space/tab/全角空格)
        # WITHOUT crossing a blank line first. Previously `[\s　]+`
        # accepted "\n\n1、章节1" — a blank line plus the next chapter
        # heading — as if it were indented body, so every preceding
        # paragraph got mis-flagged as a title.
        r"\n[ \t　]+\S",
        re.MULTILINE | re.IGNORECASE,
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
# Sequential / part markers like "（一）" "(上)" "（1）" are PRESERVED —
# they're how "后记（一）" / "后记（二）" disambiguate same-title chapters
# split across parts.
_PAREN_NOISE = re.compile(r"[（(]\s*[^（()）]*\s*[)）]")
# Detect "part" markers — ≤ 4 chars of Chinese numeral / digit /
# 上中下前后续完终. Examples kept: （一）(二)（上）(下)(中)（前）（后）
# （1）（10）. Anything longer / containing words gets stripped as noise.
_PART_MARKER = re.compile(
    r"^[（(]\s*([零〇一二两三四五六七八九十百千0-9０-９上中下前后续完终之]{1,4})\s*[)）]$"
)


def _is_part_marker(paren_text: str) -> bool:
    """True if ``paren_text`` (including its brackets) looks like a
    sequential part marker — preserve in the cleaned title."""
    return bool(_PART_MARKER.match(paren_text.strip()))


def _clean_title(s: str) -> str:
    """Remove parenthesized asides like '（求月票）' / '(求收藏)' from chapter
    titles, collapse whitespace, and trim. Sequential part markers
    like '（一）' / '(上)' / '（1）' are preserved so chapters that
    differ only by part number remain distinguishable."""
    if not s:
        return s
    def _sub(m: re.Match[str]) -> str:
        whole = m.group(0)
        return whole if _is_part_marker(whole) else ""
    out = _PAREN_NOISE.sub(_sub, s)
    out = re.sub(r"\s+", " ", out).strip()
    return out


_UNNUMBERED_PATTERNS = {"缩进对比", "作者说章节"}


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
    # Step A: hard dedup by IDENTICAL raw_marker line. If two matches
    # produce the exact same heading line (e.g. a chapter "1、想等的人"
    # repeated by mistake in the source file), keep only the
    # longest-body one — those are TRUE duplicates regardless of
    # parsed number or content length.
    by_marker: dict[str, list[int]] = {}
    for i, m in enumerate(matches):
        marker_text = m.group(0).strip().rstrip("\r")
        if not marker_text:
            continue
        by_marker.setdefault(marker_text, []).append(i)
    for marker_text, idxs in by_marker.items():
        if len(idxs) < 2:
            continue
        best = max(idxs, key=gap_to_next)
        for j in idxs:
            if j != best:
                drop_idx.add(j)

    # Step B: numbered-collision dedup. Only drop when a candidate's
    # body gap looks like a TOC entry (< 100 chars). If multiple
    # matches with the same parsed number all have substantial bodies,
    # they're legitimately separate chapters (e.g. a rename created a
    # collision) — keep them all and let cross-list dedup + outer
    # renumbering give unique ordinals.
    TOC_GAP = 100
    for n, idxs in by_num.items():
        idxs = [i for i in idxs if i not in drop_idx]
        if len(idxs) < 2:
            continue
        gaps = {j: gap_to_next(j) for j in idxs}
        short = [j for j in idxs if gaps[j] < TOC_GAP]
        long = [j for j in idxs if gaps[j] >= TOC_GAP]
        if short and long:
            for j in short:
                drop_idx.add(j)
        elif short and not long:
            best = max(idxs, key=gap_to_next)
            for j in idxs:
                if j != best:
                    drop_idx.add(j)
        # All substantial: keep all

    # Number-list filter: drop N、-style matches whose CAPTURED TITLE
    # is itself dominated by "[digit]+、" pairs — e.g. the regex
    # greedily matched "10、9、8、7、6、" as one chapter with
    # title="9、8、7、6、". That's clearly a narrative countdown, not
    # a real heading. Heuristic: title contains another N、 pattern.
    for i in range(len(matches)):
        if i in drop_idx:
            continue
        try:
            t = (matches[i].group(2) or "").strip()
        except (IndexError, error_re):
            t = ""
        if t and re.search(r"[0-9０-９]+\s*[、．]", t):
            drop_idx.add(i)

    # Tight-cluster list dedup: catches in-body number sequences like
    # "10、9、8、…" that pattern-match as N、 headings but are really
    # narrative countdown. Two passes:
    #   (a) drop every empty-title match whose gap to next is < 20.
    #   (b) drop the TRAILING empty-title match that closes such a
    #       cluster (its gap to next is larger, but it's clearly the
    #       last item of the list and not a real chapter heading).
    #
    # IMPORTANT: only applies to N、 / N. patterns. 第N章 / 第N回 / 第N节 /
    # Chapter N legitimately appear without a descriptive title (e.g.
    # "第一章\n这是正文。" — a chapter heading is just "第一章"). Their
    # short body gap is normal for terse-narrative works; treating
    # those as countdown clusters drops real chapters.
    _CLUSTER_PATTERNS = {"N、", "N."}
    TIGHT_GAP = 20
    cluster_member: list[bool] = [False] * len(matches)
    for i in range(len(matches)):
        if i in drop_idx:
            continue
        if named_matches[i][0] not in _CLUSTER_PATTERNS:
            continue
        if gap_to_next(i) >= TIGHT_GAP:
            continue
        try:
            t = (matches[i].group(2) or "").strip()
        except (IndexError, error_re):
            t = ""
        if not t:
            drop_idx.add(i)
            cluster_member[i] = True
            if i + 1 < len(matches):
                cluster_member[i + 1] = True
    # Pass (b): empty-title matches flagged as "cluster trailer" — the
    # match that came right after a dropped cluster member, which is
    # the natural last item of the list.
    for i in range(len(matches)):
        if i in drop_idx or not cluster_member[i]:
            continue
        try:
            t = (matches[i].group(2) or "").strip()
        except (IndexError, error_re):
            t = ""
        if not t:
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

    # Chapters are already in document order. Final adjacent dedup:
    # only collapse adjacent NUMBERED chapters with the same parsed
    # number AND one of them has very short content (TOC entry).
    # If both have substantial bodies, they're two legit chapters
    # (e.g. a renamed chapter that collides with a later one) — keep
    # both, the cross-list pass below also preserves them.
    SHORT_BODY = 100
    deduped: list[dict] = []
    for c in chapters:
        if (
            deduped
            and c["pattern"] not in _UNNUMBERED_PATTERNS
            and deduped[-1]["pattern"] == c["pattern"]
            and deduped[-1]["number"] == c["number"]
        ):
            prev_len = len(deduped[-1]["content"])
            cur_len = len(c["content"])
            if prev_len <= SHORT_BODY or cur_len <= SHORT_BODY:
                # At least one is short — treat as TOC dup, keep longer.
                if cur_len > prev_len:
                    deduped[-1] = c
                continue
            # Both substantial — keep both
        deduped.append(c)

    # Cross-list dedup, two-tier:
    #   Tier 1 (always): identical raw_marker line → keep longer body.
    #   Tier 2 (TOC catch-all): same (pattern, parsed_number) where at
    #          least one side has TOC-shaped (<100 char) body → drop
    #          the TOC entry; otherwise both are kept.
    SHORT_CONTENT = 100
    keep_mask = [True] * len(deduped)
    # Tier 1: by raw_marker
    seen_by_marker: dict[str, int] = {}
    for idx, c in enumerate(deduped):
        mk = (c.get("raw_marker") or "").strip()
        if not mk:
            continue
        if mk in seen_by_marker:
            prev_idx = seen_by_marker[mk]
            if len(c["content"]) > len(deduped[prev_idx]["content"]):
                keep_mask[prev_idx] = False
                seen_by_marker[mk] = idx
            else:
                keep_mask[idx] = False
        else:
            seen_by_marker[mk] = idx

    # Tier 2: by (pattern, parsed_number) — only fires if a TOC entry
    # snuck through (one side has very short content).
    seen_by_key: dict[tuple, int] = {}
    for idx, c in enumerate(deduped):
        if not keep_mask[idx]:
            continue
        if c["pattern"] in _UNNUMBERED_PATTERNS:
            continue
        key = (c["pattern"], c["number"])
        if key in seen_by_key:
            prev_idx = seen_by_key[key]
            prev_len = len(deduped[prev_idx]["content"])
            cur_len = len(c["content"])
            if prev_len > SHORT_CONTENT and cur_len > SHORT_CONTENT:
                # Both real — keep both, don't update canonical.
                continue
            if cur_len > prev_len:
                keep_mask[prev_idx] = False
                seen_by_key[key] = idx
            else:
                keep_mask[idx] = False
        else:
            seen_by_key[key] = idx
    deduped = [c for i, c in enumerate(deduped) if keep_mask[i]]
    # Rescue split removed — was over-splitting normal long chapters
    # into derived pieces, producing apparent "duplicate" chapter
    # entries (e.g. user reported 1、 to 1000、 each appearing twice).
    # If a chapter is unusually long, surface it via is_length_outlier
    # so the user can decide manually rather than auto-splitting.

    # Assign ordinals. For NUMBERED chapters the ordinal IS the parsed
    # number (so if "2、" is missing in the source, the chapter labeled
    # "3、" shows as #3 and the gap detector flags the missing #2). For
    # unnumbered chapters we synthesize a position-based ordinal that
    # falls between its numbered neighbors — keeps document order
    # while preserving the visible gap of numbered chapters.
    last_numbered = 0
    next_unnumbered_offset = 1
    for i, c in enumerate(deduped):
        c["index"] = i
        # Capture the parsed number once
        if "parsed_number" not in c:
            c["parsed_number"] = (
                c["number"] if c["pattern"] not in _UNNUMBERED_PATTERNS else None
            )
        pn = c.get("parsed_number")
        if isinstance(pn, int):
            c["number"] = pn
            last_numbered = pn
            next_unnumbered_offset = 1
        else:
            # Unnumbered: park it just after the last numbered chapter
            # we saw. Multiple unnumbered between two numbered get
            # consecutive offsets.
            c["number"] = last_numbered + next_unnumbered_offset
            next_unnumbered_offset += 1

    # Unique chapter_id per record — stable within this detection run
    # and used by edit / delete endpoints to address a specific chapter
    # without depending on `number` (which can collide when two
    # patterns produce the same parsed numeral). Format: "c{ord}" where
    # ord is the position in the final deduped list. Cheaper than
    # UUIDs, easier to debug, perfectly unique per session.
    for i, c in enumerate(deduped):
        c["chapter_id"] = f"c{i + 1}"
        # `display_number` is the parsed numeral the user sees as
        # "第N章" / "N、". Identical to `number` UNLESS we had to bump
        # `number` to break a uniqueness collision (kept for back-compat
        # with the legacy /chapter/{number} endpoints).
        c["display_number"] = c.get("number")

    # FINAL legacy `number` uniqueness pass — kept so any code path
    # still keyed on `number` doesn't silently collide. New code should
    # use `chapter_id` instead.
    used: set[int] = set()
    for c in deduped:
        n = c.get("number")
        if not isinstance(n, int):
            continue
        if n in used:
            bumped = n + 1
            while bumped in used:
                bumped += 1
            c["number_collision"] = n
            c["number"] = bumped
        used.add(c["number"])

    return deduped


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
    # Normalize Windows / classic-Mac line endings BEFORE pattern matching.
    # Many uploaded novel files are CRLF — without this the `$` anchor
    # never matched (the regex saw "第一章 序章\r\n" with a trailing \r
    # that fails `[^\s…]$`). Patterns were silently 0-match on such files.
    if "\r" in text:
        text = text.replace("\r\n", "\n").replace("\r", "\n")
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
    #   - OTHER numbered built-in patterns with ≥ 1 match. Even a
    #     single 第740章 in a file dominated by 1、-style headings
    #     deserves to surface; our patterns are tight enough now that
    #     body-text false positives are very rare. Cross-pattern
    #     overlap dedup below drops anything that collides with a
    #     primary match.
    primary_named: list[tuple[str, re.Match[str]]] = [
        (best_name or "", m) for m in best_matches
    ]
    secondary_named: list[tuple[str, re.Match[str]]] = []
    for name, pat in all_patterns:
        if name == best_name:
            continue
        if name in custom_names or name == "作者说章节":
            for m in pat.finditer(text):
                secondary_named.append((name, m))
            continue
        # Other built-in numbered patterns — merge ALL matches.
        if name in _UNNUMBERED_PATTERNS:
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


def find_chapter_gaps(chapters: list[dict]) -> list[dict]:
    """Scan the chapter list for parsed_number discontinuities. Returns
    a list of gap descriptors so the UI can highlight where chapters
    are missing.

    A gap is a place where the parsed_number jumps by more than 1
    (e.g. ..., 4, 6, ... → gap of 5 missing). Unnumbered chapters are
    skipped — they don't define a sequence. The scan is per-pattern
    (第N章 chapters compared to 第N章 chapters; N、 to N、) so mixing
    formats doesn't produce false gaps.

    Each gap descriptor:
      {after_number, expected, missing_numbers, missing_count, pattern}
    where ``after_number`` is the ordinal of the chapter the gap
    appears after.
    """
    out: list[dict] = []
    # Group by pattern so two different formats don't compare
    by_pattern: dict[str, list[dict]] = {}
    for c in chapters:
        pat = c.get("pattern")
        if not pat or pat in _UNNUMBERED_PATTERNS:
            continue
        pn = c.get("parsed_number")
        if not isinstance(pn, int):
            continue
        by_pattern.setdefault(pat, []).append(c)
    for pat_name, chs in by_pattern.items():
        for i in range(1, len(chs)):
            prev_pn = chs[i - 1].get("parsed_number")
            cur_pn = chs[i].get("parsed_number")
            if not (isinstance(prev_pn, int) and isinstance(cur_pn, int)):
                continue
            if cur_pn - prev_pn > 1:
                missing = list(range(prev_pn + 1, cur_pn))
                # Cap the displayed missing list so a huge jump doesn't
                # render thousands of numbers in a chip.
                preview_missing = missing[:8]
                out.append({
                    "after_number": chs[i - 1].get("number"),
                    "before_number": chs[i].get("number"),
                    "expected_next": prev_pn + 1,
                    "missing_numbers": preview_missing,
                    "missing_count": len(missing),
                    "pattern": pat_name,
                })
    return out


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


# Built-in patterns for garbled / non-text noise commonly seen in
# scraped novel files. Each pattern is intentionally narrow so it
# matches actual scrape leftovers without eating real Chinese text.
_BUILTIN_GARBLED_PATTERNS: list[tuple[str, str]] = [
    # HTML-style noise
    ("HTML 注释", r"<!--.*?-->"),
    ("HTML 转义", r"&(?:lt|gt|amp|quot|apos|nbsp|#\d+|#x[0-9a-fA-F]+);"),
    ("HTML 标签", r"<\/?[a-zA-Z][a-zA-Z0-9]*(?:\s[^>]*)?>"),
    # Markup / template fragments
    ("模板占位", r"\{\{[^}]+\}\}"),
    ("BBCode 标签", r"\[/?[a-zA-Z][^\]]*\]"),
    # Random scrape-watermark IDs — mixed letters + digits, 12+ chars
    # (catches "3Abq5FQkXVhYkEy8u3y"-style noise even when adjacent to
    # CJK text). NOTE: Python `\w` includes Chinese chars, so we cannot
    # use `\b` here — it fails between CJK and ASCII. Use explicit
    # alphanumeric-or-start/end-of-string lookarounds instead.
    ("随机 ID", r"(?:^|[^A-Za-z0-9])((?=[A-Za-z0-9]*[A-Za-z])(?=[A-Za-z0-9]*[0-9])[A-Za-z0-9]{12,})(?:[^A-Za-z0-9]|$)"),
    # Known site watermarks
    ("追书神器水印", r"@追书神器|#追书神器|追书神器"),
    # Classic Chinese mojibake fingerprints — these strings appear when
    # the source bytes were decoded with the wrong codec. Their
    # presence alone is strong evidence of mojibake.
    ("锟斤拷", r"锟[斤拷]+"),
    ("乱码替换符", r"[�￾￿]{2,}"),  # U+FFFD replacement etc.
    # Box-drawing chars in CJK runs — often signal Latin-1 misdecode.
    ("方块/控制字符", r"[■-◿]{3,}|[\x00-\x08\x0b\x0c\x0e-\x1f]+"),
    # Zero-width / soft-hyphen artifacts (BOM, ZWNJ, ZWJ, …)
    ("零宽字符", r"[​-‏‪-‮⁠-⁯﻿]+"),
    # Stray Latin-1 high bytes that survived re-decode (Mojibake fingerprint)
    ("Latin-1 残留", r"[À-ÿ]{4,}"),
    # Long runs of ? at end of paragraph (often last-byte loss)
    ("问号尾巴", r"\?{4,}"),
]


def _load_user_garbled_patterns() -> list[dict]:
    """Read user-customized garbled patterns from settings.json.
    Returns a list of {name, regex, enabled}. Empty list when none
    configured."""
    try:
        from pathlib import Path as _P
        root = _P(__file__).resolve().parents[2]
        sp = root / "data" / "settings.json"
        if not sp.exists():
            return []
        import json as _json
        data = _json.loads(sp.read_text(encoding="utf-8"))
        raw = data.get("garbled_patterns")
        return [x for x in raw if isinstance(x, dict)] if isinstance(raw, list) else []
    except Exception:
        return []


def _compile_garbled() -> list[tuple[str, "re.Pattern[str]"]]:
    """Return the effective garbled-pattern list: built-ins + enabled
    user patterns, all compiled."""
    out: list[tuple[str, "re.Pattern[str]"]] = []
    for name, pattern in _BUILTIN_GARBLED_PATTERNS:
        try:
            out.append((name, re.compile(pattern, re.DOTALL | re.IGNORECASE)))
        except re.error:
            continue
    for p in _load_user_garbled_patterns():
        if not isinstance(p, dict) or p.get("enabled") is False:
            continue
        name = (p.get("name") or "自定义").strip()
        regex = (p.get("regex") or "").strip()
        if not regex:
            continue
        try:
            out.append((name, re.compile(regex, re.DOTALL | re.IGNORECASE)))
        except re.error:
            continue
    return out


# Live-recompiled list each access via the function; callers should
# invoke ``_compile_garbled()`` to pick up CRUD changes without a
# server restart.


# ── Encoding-mojibake repair ──
#
# Six common Chinese-novel mojibake patterns the user listed:
#   1. 古文夹杂日韩文     → GBK bytes read as UTF-8 → encode utf-8, decode gbk
#   2. 方块形               → UTF-8 bytes read as GBK → encode gbk, decode utf-8
#   3. 各种符号             → ISO8859-1 read as UTF-8 → encode latin-1, decode utf-8
#   4. 拼音码（带声调）     → ISO8859-1 read as GBK → encode latin-1, decode gbk
#   5. 奇数长度末尾问号     → GBK as UTF-8 then UTF-8 (double round trip)
#   6. 大量"锟斤拷"         → UTF-8 as GBK then GBK (double round trip)


def _try_decode(text: str, encode_with: str, decode_with: str) -> str | None:
    """Attempt a faux-encode then decode round trip. Returns None when
    either side fails — mojibake fixes that don't apply are skipped."""
    try:
        return text.encode(encode_with, errors="strict").decode(decode_with, errors="strict")
    except (UnicodeEncodeError, UnicodeDecodeError, LookupError):
        return None


def _cjk_density(text: str) -> float:
    """Fraction of CJK ideograph characters in ``text``. Used to score
    mojibake-repair candidates — the winning transform should leave
    the most Chinese readable."""
    if not text:
        return 0.0
    cjk = sum(1 for c in text
              if "一" <= c <= "鿿"
              or "㐀" <= c <= "䶿")
    return cjk / max(1, len(text))


_ENCODING_REPAIRS: list[tuple[str, "callable"]] = [
    # 1. Source was GBK, decoder was UTF-8
    ("GBK→UTF-8 (古文夹杂日韩文)", lambda t: _try_decode(t, "utf-8", "gbk")),
    # 2. Source was UTF-8, decoder was GBK
    ("UTF-8→GBK (方块形)", lambda t: _try_decode(t, "gbk", "utf-8")),
    # 3. Source was UTF-8, decoder was ISO-8859-1
    ("UTF-8→Latin-1 (各种符号)", lambda t: _try_decode(t, "latin-1", "utf-8")),
    # 4. Source was GBK, decoder was ISO-8859-1
    ("GBK→Latin-1 (拼音码)", lambda t: _try_decode(t, "latin-1", "gbk")),
    # 5. Double round trip for odd-length / trailing-? case
    ("GBK→UTF-8 双次 (奇数长度问号)", lambda t: (
        (lambda s1: _try_decode(s1, "utf-8", "utf-8") if s1 else None)(
            _try_decode(t, "utf-8", "gbk")
        )
    )),
    # 6. Double round trip for 锟斤拷-dominated text
    ("UTF-8→GBK 双次 (锟斤拷)", lambda t: (
        (lambda s1: _try_decode(s1, "gbk", "gbk") if s1 else None)(
            _try_decode(t, "gbk", "utf-8")
        )
    )),
]


def detect_encoding_mojibake(text: str) -> dict | None:
    """Try each of the 6 encoding-repair transforms and pick the one
    that produces the highest CJK density INCREASE versus the input.

    Returns ``{transform, before_cjk, after_cjk, fixed_text}`` when a
    repair is worth applying, or None when no transform improves the
    text materially (delta < 5% CJK)."""
    base_density = _cjk_density(text)
    best: dict | None = None
    for name, fix in _ENCODING_REPAIRS:
        try:
            candidate = fix(text)
        except Exception:
            candidate = None
        if not candidate or candidate == text:
            continue
        cand_density = _cjk_density(candidate)
        # Threshold loosened to 3% so subtle mojibake (e.g. a chapter
        # with some misdecoded substrings) still gets caught.
        if cand_density - base_density < 0.03:
            continue
        if not best or cand_density > best["after_cjk"]:
            best = {
                "transform": name,
                "before_cjk": base_density,
                "after_cjk": cand_density,
                "fixed_text": candidate,
            }
    return best


def repair_encoding(text: str) -> tuple[str, str | None]:
    """Best-effort encoding repair. Returns (fixed_text, transform_name).
    transform_name is None when no transform helped (text is returned
    unchanged)."""
    result = detect_encoding_mojibake(text)
    if not result:
        return text, None
    return result["fixed_text"], result["transform"]


def flag_garbled_chapters(chapters: list[dict]) -> list[dict]:
    """Mark each chapter with ``is_garbled`` + ``garbled_reasons`` based
    on whether its content contains noise like ``<!--go-->`` /
    ``&lt;`` / HTML tags / BBCode left over from web scraping.
    Reasons are short human-readable labels with the first matched
    sample, suitable for tooltip + filter chip display."""
    patterns = _compile_garbled()
    for c in chapters:
        content = c.get("content") or ""
        reasons: list[str] = []
        encoding_hint: str | None = None
        for label, pat in patterns:
            ms = pat.findall(content)
            if not ms:
                continue
            first = ms[0]
            if isinstance(first, tuple):
                first = first[0] if first else ""
            sample = first[:30].replace("\n", " ").strip()
            reasons.append(f"{label}（{len(ms)}处｜样本：{sample}）")
        # Per-chapter encoding-mojibake test — fires even when no regex
        # matched (sometimes the whole chapter is mojibake'd but has
        # no specific marker). We try the 6 repair transforms and if
        # any one significantly improves CJK density, we flag.
        if content and not reasons:
            try:
                guess = detect_encoding_mojibake(content)
            except Exception:
                guess = None
            if guess:
                encoding_hint = guess["transform"]
                reasons.append(
                    f"疑似编码乱码（建议：{guess['transform']}｜"
                    f"CJK 密度 {int(guess['before_cjk']*100)}% → "
                    f"{int(guess['after_cjk']*100)}%）"
                )
        c["is_garbled"] = bool(reasons)
        c["garbled_reasons"] = reasons
        if encoding_hint:
            c["encoding_repair_hint"] = encoding_hint
    return chapters


def strip_garbled(text: str, try_encoding_repair: bool = True) -> str:
    """Two-step cleanup:

      1. Run each regex in the effective garbled-pattern set (built-in
         + user CRUD) and ``sub("")`` the matches.
      2. If ``try_encoding_repair`` is True, attempt the 6 mojibake
         repairs and apply whichever one materially increases the CJK
         density of the result.

    Whitespace runs created by the deletions get collapsed before
    returning."""
    for _, pat in _compile_garbled():
        text = pat.sub("", text)
    if try_encoding_repair:
        fixed, _ = repair_encoding(text)
        text = fixed
    text = re.sub(r"[ \t　]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


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
    # Horizontal-rule markers signaling "everything after this is an
    # author aside": ---/===/***/——/＝＝ runs of ≥ 3 chars on their own
    # line. Web-novel authors use these to fence off PS / 求月票 etc.
    rule_pat = re.compile(r"^[\s　]*([-=*＝—─]{3,})[\s　]*$")
    out: list[dict] = []
    for c in chapters:
        if c.get("pattern") == "作者说章节":
            continue  # whole-chapter asides — handled by the other button
        content = c.get("content") or ""
        if not content:
            continue
        paragraphs = re.split(r"\n\s*\n", content)
        n_paras = len(paragraphs)
        # Locate the FIRST horizontal-rule paragraph; everything after
        # it (up to chapter end) is treated as author-aside trailing
        # block — fence-style asides that web-novel authors append
        # without using the keyword vocabulary.
        fence_idx: int | None = None
        for pi, para in enumerate(paragraphs):
            for line in para.splitlines():
                if rule_pat.match(line):
                    fence_idx = pi
                    break
            if fence_idx is not None:
                break
        for pi, para in enumerate(paragraphs):
            ps = para.strip()
            if not ps:
                continue
            # Skip long paragraphs — narrative, not asides.
            if len(ps) > max_para_chars:
                continue
            hits = [kw for kw in keywords if kw in ps]
            after_fence = fence_idx is not None and pi >= fence_idx
            short_aside = after_fence and len(ps) < 200
            if not hits and not short_aside:
                continue
            reasons: list[str] = []
            if hits:
                reasons.append("命中：" + "、".join(hits[:3])
                                + (f"…(+{len(hits) - 3})" if len(hits) > 3 else ""))
            if after_fence:
                reasons.append(
                    "分割线（---）之后" if pi > (fence_idx or 0)
                    else "章节末分割线"
                )
            is_at_end = pi >= n_paras - 2
            if is_at_end and "位于章节末尾" not in reasons:
                reasons.append("位于章节末尾")
            score = (
                len(hits)
                + (2 if is_at_end else 0)
                + (1 if len(ps) < 100 else 0)
                + (3 if after_fence else 0)
            )
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


def insert_chapter(text: str, chapters: list[dict],
                    after_number: int | None,
                    heading: str, content: str) -> str:
    """Rebuild the file with a new chapter inserted after ``after_number``
    (None or 0 = insert at the very start). The new chapter's heading
    line is appended as-is — the user supplies the format they want
    (e.g. "147、新章节" or "第一百四十七章 重生"). Detection re-runs on
    the new file and assigns the chapter its sequential ordinal.
    """
    new_heading = (heading or "").strip()
    new_body = (content or "").strip()
    if not new_heading:
        raise ValueError("章节标题不能为空")
    parts: list[str] = []
    inserted = False
    if after_number in (None, 0):
        parts.append(new_heading)
        if new_body:
            parts.append(new_body)
        inserted = True
    for c in chapters:
        marker = c.get("raw_marker") or c.get("title") or ""
        body = c.get("content") or ""
        if marker:
            parts.append(marker)
        if body:
            parts.append(body)
        if not inserted and c.get("number") == after_number:
            parts.append(new_heading)
            if new_body:
                parts.append(new_body)
            inserted = True
    if not inserted:
        # after_number not found — append at end
        parts.append(new_heading)
        if new_body:
            parts.append(new_body)
    return "\n\n".join(p for p in parts if p)


def rename_chapter(text: str, chapters: list[dict],
                    chapter_number: int, new_heading: str) -> str:
    """Replace the heading line of one chapter with ``new_heading``.
    Body is preserved. Detection re-runs after to refresh the title.
    """
    nh = (new_heading or "").strip()
    if not nh:
        raise ValueError("章节标题不能为空")
    parts: list[str] = []
    found = False
    for c in chapters:
        marker = c.get("raw_marker") or c.get("title") or ""
        if c.get("number") == chapter_number:
            marker = nh
            found = True
        body = c.get("content") or ""
        if marker:
            parts.append(marker)
        if body:
            parts.append(body)
    if not found:
        raise ValueError(f"未找到第 {chapter_number} 章")
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
