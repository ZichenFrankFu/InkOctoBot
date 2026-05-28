"""Per-work neologism extraction — 4-path recall + dictionary filter +
LLM classification.

**Strict scope** (spec § 七 ★): single-work only. Do NOT aggregate
across works; do NOT mine naming patterns; do NOT feed back into
Writer naming. Output goes to ``work_neologisms`` and is consumed
by Loader 3 (reference) only.
"""
from __future__ import annotations

import json
import logging
import math
import re
import sqlite3
import uuid
from collections import Counter
from pathlib import Path
from typing import Any

from .dictionaries import load_genre_dict

logger = logging.getLogger("inkoctobot.market_extractor.neologism_extractor")


_PROMPT_TEMPLATE = (
    Path(__file__).resolve().parent / "resources" / "prompts"
    / "neologism_classification.txt"
)

# Min frequency across the 5-chapter window for a candidate to count.
_MIN_FREQUENCY = 2
# Cap candidates sent to the LLM — keep prompt small.
_MAX_CANDIDATES_PER_WORK = 60


# Common surnames / particles that should never qualify as neologisms.
_STOP_PREFIXES = ("的", "了", "是", "在", "和", "与", "或", "但")


# ─────────── recall paths ───────────


def _recall_via_jieba(text: str) -> set[str]:
    try:
        import jieba
    except Exception:
        return set()
    return {
        t for t in jieba.cut(text)
        if 2 <= len(t) <= 6 and re.match(r"^[一-鿿]+$", t)
    }


def _ngram_pmi(text: str, max_n: int = 4) -> set[str]:
    """Tiny PMI-style recall — bigrams/trigrams that co-occur far more
    than chance + have varied left-right neighbors. Crude but fast."""
    cands: set[str] = set()
    chars = [c for c in text if "一" <= c <= "鿿"]
    if len(chars) < 50:
        return cands
    unigram = Counter(chars)
    total = sum(unigram.values())
    for n in (2, 3, 4):
        if n > max_n:
            break
        ngrams = Counter()
        for i in range(len(chars) - n + 1):
            ngrams[tuple(chars[i:i + n])] += 1
        for gram, count in ngrams.items():
            if count < _MIN_FREQUENCY:
                continue
            # Crude PMI: log(p(gram) / prod(p(char))).
            p_gram = count / total
            prod = 1.0
            for c in gram:
                prod *= unigram[c] / total
            if prod == 0:
                continue
            pmi = math.log(p_gram / prod)
            if pmi > 4:
                cands.add("".join(gram))
    return cands


def _unknown_word_filter(text: str) -> set[str]:
    """Anything 2-6 chars that looks like a name (consecutive CJK,
    not a single common char) and not already in the genre dict."""
    cands: set[str] = set()
    for m in re.finditer(r"[一-鿿]{2,6}", text):
        w = m.group(0)
        if any(w.startswith(p) for p in _STOP_PREFIXES):
            continue
        cands.add(w)
    return cands


def _char_pattern_match(text: str) -> set[str]:
    """Heuristic: words ending in canonical neologism suffixes
    (国/宗/教/派/会/族/帝/王/皇/术/法/诀/药/丹/丸/经/书/谷/界/...)."""
    suffixes = "国宗教派会族帝王皇术法诀药丹丸经书谷界宫殿庙观院门城关山岭洞府卷篇章决"
    cands: set[str] = set()
    for m in re.finditer(rf"[一-鿿]{{1,4}}[{suffixes}]", text):
        w = m.group(0)
        if 2 <= len(w) <= 6:
            cands.add(w)
    return cands


# ─────────── filter + LLM ───────────


def recall_candidates(
    chapter_texts: dict[int, str],
) -> dict[str, dict]:
    """Run all 4 recall paths over the 5-chapter window. Returns
    ``{term: {frequency, first_chapter, first_position, contexts}}``.

    Two-pass design so per-chapter regex boundary noise doesn't drop
    cross-chapter recurring terms:
    Pass 1 — union every candidate from every chapter into one set.
    Pass 2 — for each candidate term, count occurrences across all
             chapter texts and capture first-chapter + context samples.
    """
    all_terms: set[str] = set()
    for text in chapter_texts.values():
        all_terms |= _recall_via_jieba(text)
        all_terms |= _ngram_pmi(text)
        all_terms |= _unknown_word_filter(text)
        all_terms |= _char_pattern_match(text)

    out: dict[str, dict] = {}
    sorted_chapters = sorted(chapter_texts.items())
    for term in all_terms:
        total_freq = 0
        first_chapter: int | None = None
        first_position = ""
        contexts: list[str] = []
        for chap_num, text in sorted_chapters:
            freq = text.count(term)
            if freq == 0:
                continue
            total_freq += freq
            if first_chapter is None:
                first_chapter = chap_num
                idx = text.find(term)
                first_position = f"ch{chap_num}:{idx}"
            if len(contexts) < 2:
                idx = text.find(term)
                if idx >= 0:
                    start = max(0, idx - 20)
                    end = min(len(text), idx + len(term) + 20)
                    contexts.append(text[start:end])
        if total_freq < _MIN_FREQUENCY:
            continue
        out[term] = {
            "frequency":      total_freq,
            "first_chapter":  first_chapter or 0,
            "first_position": first_position,
            "contexts":       contexts,
        }
    return out


def filter_by_genre_dict(
    candidates: dict[str, dict], category: str,
) -> dict[str, dict]:
    """Strip out candidates that already appear in the genre vocabulary —
    those are common terms, not work-specific neologisms."""
    genre_dict = load_genre_dict(category)
    return {t: m for t, m in candidates.items() if t not in genre_dict}


async def classify_with_llm(
    candidates: dict[str, dict], category: str,
    *, work_id: str = "", db_path: str | None = None,
) -> tuple[list[dict], str]:
    """Send the top-N candidates to the LLM for is_work_specific +
    type classification. Returns the classified list + model label."""
    if not candidates:
        return [], ""

    sorted_cands = sorted(
        candidates.items(), key=lambda x: x[1]["frequency"], reverse=True,
    )[:_MAX_CANDIDATES_PER_WORK]

    lines = []
    for term, meta in sorted_cands:
        ctxs = "\n  ".join(meta["contexts"][:2])
        lines.append(
            f"- {term} (出现 {meta['frequency']} 次, 首次 第{meta['first_chapter']} 章)\n  {ctxs}"
        )
    prompt = _PROMPT_TEMPLATE.read_text("utf-8").format(
        candidates="\n".join(lines),
    )

    from llm.call_site import LLMCallSite
    from llm.router import ModelRouter
    from .llm_extractor import _extract_json
    cs = LLMCallSite(
        call_site_id="market_extractor.neologism_classify",
        primary_role="post_commit",
        parsed_target_table="work_neologisms",
        default_max_tokens=2500, default_temperature=0.2,
    )
    raw = await cs.invoke(
        prompt=prompt,
        system="你是网文专有名词分析专家。只输出 JSON。",
        project_id=work_id, db_path=db_path,
    )
    parsed = _extract_json(raw)
    classified = parsed.get("neologisms") or []
    provider, model = ModelRouter().resolve_role("post_commit")
    return classified, f"{provider}/{model}".strip("/")


def persist(
    db_path: str, work_id: str,
    candidates_meta: dict[str, dict],
    classified: list[dict], llm_model: str,
) -> list[str]:
    """Write ``is_work_specific=True`` classified rows to ``work_neologisms``."""
    written: list[str] = []
    allowed_types = {
        "character", "location", "organization", "item",
        "concept", "title", "deity", "event", "other",
    }
    with sqlite3.connect(db_path) as con:
        for c in classified:
            if not c.get("is_work_specific"):
                continue
            term = str(c.get("term") or "").strip()
            if not term:
                continue
            meta = candidates_meta.get(term) or {}
            ntype = str(c.get("type") or "other").strip().lower()
            if ntype not in allowed_types:
                ntype = "other"
            nid = f"wn_{uuid.uuid4().hex[:12]}"
            try:
                con.execute(
                    "INSERT OR REPLACE INTO work_neologisms "
                    "(neologism_id, work_id, term, neologism_type, "
                    " first_chapter, first_position, "
                    " frequency_in_5_chapters, definition_hint, "
                    " context_samples_json, llm_model_used) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (nid, work_id, term, ntype,
                     meta.get("first_chapter") or 0,
                     meta.get("first_position") or "",
                     meta.get("frequency") or 0,
                     str(c.get("definition_hint") or "").strip(),
                     json.dumps(meta.get("contexts") or [], ensure_ascii=False),
                     llm_model),
                )
                written.append(nid)
            except sqlite3.IntegrityError:
                # work_id+term already present (re-extraction); skip silently.
                pass
        con.commit()
    return written


async def extract_for_work(
    db_path: str, work_id: str, category: str,
    chapter_texts: dict[int, str],
) -> dict[str, Any]:
    """Full pipeline. Returns summary stats."""
    candidates = recall_candidates(chapter_texts)
    filtered = filter_by_genre_dict(candidates, category)
    if not filtered:
        return {"recalled": len(candidates), "filtered": 0, "saved": 0}
    classified, model_label = await classify_with_llm(
        filtered, category, work_id=work_id, db_path=db_path,
    )
    written = persist(db_path, work_id, filtered, classified, model_label)
    return {
        "recalled":   len(candidates),
        "filtered":   len(filtered),
        "classified": len(classified),
        "saved":      len(written),
        "llm_model":  model_label,
    }
