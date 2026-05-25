"""
Volume (分卷) detection.

Three layers, in priority order:

  1. ``ai_detect``      — call the LLM (web-search-capable role preferred)
                          and ask it to identify volume boundaries against
                          the official source. Highest accuracy when the
                          work is publicly indexed.
  2. ``text_detect``    — rule-based scan of chapter titles AND first lines
                          of chapter content for volume markers (第X卷 /
                          第N部 / Book N / 上部 / 下部 / 序章). Used when
                          no AI is available, or as a fast pre-check.
  3. ``parser_detect``  — existing per-chapter ``volume`` tag set by the
                          chapter splitter. Same as the legacy detector.

All three return ``list[dict]`` of ``{title, start_chapter, end_chapter}``
(1-based, contiguous) or ``None`` if no convincing structure was found.

The strict-JSON style mirrors ``ai_extractor.py``: the LLM call uses a
system message + ``response_format=json_object`` so we get parseable
output even on chat-tuned models that otherwise drift into prose.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

logger = logging.getLogger("inkoctobot.analysis.volume_detector")


# ── Layer 1: AI detection ────────────────────────────────────────────


def _build_titles_listing(chapters: list[dict], max_chars: int = 8000) -> str:
    """Compact representation of chapter titles for the AI prompt.

    Each row: ``#N: <title>``. Truncated when over ``max_chars`` so the
    prompt stays in token budget even on 5000-chapter web novels."""
    out: list[str] = []
    total = 0
    for i, ch in enumerate(chapters, start=1):
        title = (ch.get("title") or "").strip()[:60] or "(无标题)"
        line = f"#{i}: {title}"
        if total + len(line) + 1 > max_chars:
            out.append("…[更多章节省略]…")
            break
        out.append(line)
        total += len(line) + 1
    return "\n".join(out)


def _format_author_hint(creator: str | None) -> str:
    if creator and creator.strip():
        return f"- 作者：{creator.strip()}"
    return "- 作者：未知（请通过标题检索）"


def build_volume_prompt(work: dict, chapters: list[dict]) -> str:
    """Render the volume-detect prompt for this work. Exposed so the UI
    can show the user the exact prompt that would be sent — they can
    copy it into a web-based LLM if our model fails."""
    from analysis.feature_extraction.prompts import render
    return render(
        "reference.volume_detect",
        title=work.get("title") or "(未命名)",
        author_hint=_format_author_hint(work.get("creator")),
        n_chapters=len(chapters),
        titles=_build_titles_listing(chapters),
    )


async def ai_detect_volumes(
    work: dict,
    chapters: list[dict],
    router: Any,
    *,
    prefer_web_search: bool = True,
) -> tuple[list[dict] | None, str]:
    """Ask the LLM to identify volume boundaries.

    Returns ``(volumes, method)`` where ``method`` is one of
    ``"ai_web_search"`` / ``"ai_local"`` / ``"ai_error"``. ``volumes`` is
    ``None`` when the call fails or the response can't be coerced into a
    contiguous plan.

    No NLP fallback inside this layer — the caller decides whether to
    drop to ``text_detect_volumes`` on None.
    """
    from analysis.feature_extraction import ai_extractor

    prompt = build_volume_prompt(work, chapters)
    use_web = False
    if prefer_web_search:
        try:
            from llm.web_search_capabilities import supports_web_search
            provider, model = router.resolve_role("reference_web_search")
            use_web = supports_web_search(provider, model)
        except Exception as e:
            logger.debug("[volume_detect] web_search probe failed: %s", e)

    method = "ai_web_search" if use_web else "ai_local"
    try:
        raw = await ai_extractor._invoke(
            router, prompt, max_tokens=4096,
            use_web_search=use_web, expect="object",
            role="reference_volume_detect",
        )
        obj = ai_extractor._parse_obj(raw)
    except Exception as e:
        logger.warning("[volume_detect] AI call failed: %s", e)
        return None, "ai_error"

    raw_vols = obj.get("volumes")
    if not isinstance(raw_vols, list) or len(raw_vols) < 2:
        logger.info("[volume_detect] AI returned < 2 volumes — treated as no-result")
        return None, method
    return _normalize_volume_list(raw_vols, len(chapters)), method


# ── Layer 2: rule-based text detection ───────────────────────────────


# Matches a wide range of Chinese / English volume markers. Each pattern's
# group 1 is the volume index hint (used only to dedupe consecutive
# matches), group 2 is the optional volume name.
_TEXT_VOLUME_PATS: tuple[re.Pattern[str], ...] = (
    # 第N卷 / 第N部
    re.compile(
        r"^[\s　]*第\s*([零〇一二两三四五六七八九十百千万0-9]+)\s*[卷部册]"
        r"[\s：:　.、，,．]*([^\n]{0,60})$",
        re.MULTILINE,
    ),
    # 卷N / 卷一
    re.compile(
        r"^[\s　]*卷\s*([零〇一二两三四五六七八九十百千万0-9]+)"
        r"[\s：:　.、，,．]*([^\n]{0,60})$",
        re.MULTILINE,
    ),
    # 上部 / 中部 / 下部 / 上篇 / 中篇 / 下篇
    re.compile(r"^[\s　]*([上中下])[篇部][\s：:　.、，,．]*([^\n]{0,60})$", re.MULTILINE),
    # 序章 / 序幕 (always implies start)
    re.compile(r"^[\s　]*(序)[章幕篇][\s：:　.、，,．]*([^\n]{0,60})$", re.MULTILINE),
    # Book N / Part N / Volume N (en)
    re.compile(
        r"^[\s　]*(?:Book|Part|Volume|Vol\.?)\s*([0-9IVXLCMivxlcm]+)"
        r"[\s：:.,、—\-]*([^\n]{0,60})$",
        re.MULTILINE | re.IGNORECASE,
    ),
)


def _text_search_chapter(ch: dict) -> tuple[str, str] | None:
    """Look in a chapter's title AND first 200 chars for a volume marker.

    Returns ``(index_token, title)`` of the first match, or ``None``.
    Searching the head of content too catches works where the volume
    heading sits on its own line above the chapter heading."""
    haystack = (ch.get("title") or "") + "\n" + (ch.get("content") or "")[:200]
    for pat in _TEXT_VOLUME_PATS:
        m = pat.search(haystack)
        if m:
            return (m.group(1) or "").strip(), (m.group(2) or "").strip()
    return None


def text_detect_volumes(chapters: list[dict]) -> list[dict] | None:
    """Scan titles + chapter heads for volume markers. Pure-Python; no
    AI involved. Returns at least 2 contiguous volumes or None."""
    if len(chapters) < 4:
        return None  # too short to bother

    starts: list[tuple[int, str]] = []
    last_token = ""
    for i, ch in enumerate(chapters, start=1):
        hit = _text_search_chapter(ch)
        if not hit:
            continue
        token, title = hit
        # Dedupe: avoid registering the same volume twice when it spans
        # multiple matched lines (e.g. the title appears in both heading
        # and the next chapter's opening as a callback).
        if token and token == last_token:
            continue
        last_token = token
        starts.append((i, title or f"第{len(starts)+1}卷"))

    if len(starts) < 2:
        return None

    volumes: list[dict] = []
    for i, (start, title) in enumerate(starts):
        end = (starts[i + 1][0] - 1) if i + 1 < len(starts) else len(chapters)
        if end < start:
            # Marker drift — skip rather than emit an invalid range.
            continue
        volumes.append({
            "title": title or f"第{i+1}卷",
            "start_chapter": start,
            "end_chapter": end,
        })
    if len(volumes) < 2:
        return None
    # Patch coverage: if first volume doesn't start at 1, prepend a
    # "前传 / 引子" cover for the dangling chapters so we don't lose them.
    if volumes[0]["start_chapter"] > 1:
        volumes.insert(0, {
            "title": "序篇",
            "start_chapter": 1,
            "end_chapter": volumes[0]["start_chapter"] - 1,
        })
    return volumes


# ── Shared: validation / normalization ───────────────────────────────


def _normalize_volume_list(
    raw_vols: list[Any], n_chapters: int,
) -> list[dict] | None:
    """Coerce the AI's raw output into a contiguous, in-range list of
    ``{title, start_chapter, end_chapter}``. Returns None when nothing
    salvageable remains.

    Steps:
      1. Cast types, clamp to [1, n_chapters].
      2. Sort by start_chapter.
      3. Snap each volume's end to next volume's start-1 (close gaps).
      4. Drop entries with end < start after snapping.
    """
    out: list[dict] = []
    for v in raw_vols:
        if not isinstance(v, dict):
            continue
        try:
            sc = int(v.get("start_chapter") or 0)
            ec = int(v.get("end_chapter") or 0)
        except (TypeError, ValueError):
            continue
        sc = max(1, min(sc, n_chapters or sc))
        ec = max(1, min(ec, n_chapters or ec))
        if sc > ec:
            continue
        title = str(v.get("title") or "").strip()
        if not title:
            title = f"第{len(out)+1}卷"
        out.append({"title": title[:60], "start_chapter": sc, "end_chapter": ec})

    if not out:
        return None

    out.sort(key=lambda x: x["start_chapter"])

    # Snap: each vol ends at the next vol's start - 1; last vol covers
    # through n_chapters. This guarantees no overlap and no gap.
    for i in range(len(out) - 1):
        out[i]["end_chapter"] = max(
            out[i]["start_chapter"],
            out[i + 1]["start_chapter"] - 1,
        )
    out[-1]["end_chapter"] = max(out[-1]["start_chapter"], n_chapters)
    # First vol covers from chapter 1 (don't drop dangling head chapters).
    out[0]["start_chapter"] = 1

    # Drop any zero-length entries that survived snapping.
    out = [v for v in out if v["end_chapter"] >= v["start_chapter"]]
    if len(out) < 2:
        return None
    return out
