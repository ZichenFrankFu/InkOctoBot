"""Phase 2 LLM extraction — 15-dim feature pull per chapter.

Uses the post_commit FallbackRouter (local-first → cloud fallback)
so this module benefits from the existing Phase-1 post-commit
infrastructure. One LLM call per chapter; failures retry within
the FallbackRouter and ultimately propagate so the caller can
mark the chapter ``skipped``.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger("inkoctobot.market_extractor.llm_extractor")


_PROMPT_TEMPLATE = (
    Path(__file__).resolve().parent / "resources" / "prompts" / "chapter_features.txt"
)


def _load_prompt_template() -> str:
    return _PROMPT_TEMPLATE.read_text("utf-8")


def _extract_json(raw: str) -> dict:
    s = raw.strip()
    if s.startswith("```"):
        s = re.sub(r"^```(?:json)?\s*", "", s)
        s = re.sub(r"\s*```$", "", s)
    m = re.search(r"\{.*\}", s, flags=re.DOTALL)
    if not m:
        raise ValueError(f"no JSON object in LLM response: {raw[:200]}")
    return json.loads(m.group(0))


async def call_llm_for_chapter(
    chapter_text: str, chapter_num: int, *, is_first_chapter: bool,
    work_id: str = "", db_path: str | None = None,
) -> tuple[dict, str]:
    """Single LLM call producing the 15-dim feature dict + model label.

    Raises on parse failure or LLM error so the caller's
    retry/skip logic decides what to do next.
    """
    from llm.call_site import LLMCallSite
    from llm.router import ModelRouter
    cs = LLMCallSite(
        call_site_id="market_extractor.chapter_features",
        primary_role="post_commit",
        parsed_target_table="chapter_features",
        default_max_tokens=3000, default_temperature=0.2,
    )
    prompt = _load_prompt_template().format(
        chapter_num=chapter_num,
        is_first_chapter="是" if is_first_chapter else "否",
        chapter_text=(chapter_text or "")[:8000],
    )
    raw = await cs.invoke(
        prompt=prompt,
        system="你是网文分析专家。只输出 JSON。",
        project_id=work_id, db_path=db_path,
    )
    parsed = _extract_json(raw)
    provider, model = ModelRouter().resolve_role("post_commit")
    return parsed, f"{provider}/{model}".strip("/")


def flatten_to_columns(parsed: dict) -> dict[str, Any]:
    """Translate the LLM's nested JSON into the flat column shape that
    ``chapter_features`` expects. Tolerant: missing keys → None."""
    a1 = parsed.get("A1_protagonist_first_appearance") or {}
    a2 = parsed.get("A2_protagonist_profile") or {}
    a3 = parsed.get("A3_protagonist_cheat") or {}
    a4 = parsed.get("A4_protagonist_agency") or {}
    a5 = parsed.get("A5_drive_conflict") or {}
    b1 = parsed.get("B1_social_network") or {}
    b2 = parsed.get("B2_focus_strategy") or {}
    c1 = parsed.get("C1_worldview") or {}
    c2 = parsed.get("C2_setting_unfold") or {}
    c3 = parsed.get("C3_setting_contrast") or {}
    d1 = parsed.get("D1_opening_hook") or {}
    d2 = parsed.get("D2_pleasure_points") or {}
    e1 = parsed.get("E1_writing_style") or {}
    e2 = parsed.get("E2_emotional_tone") or {}
    f1 = parsed.get("F1_info_disclosure") or {}
    f2 = parsed.get("F2_first_arc") or {}
    g1 = parsed.get("G1_chapter_end_hook") or {}

    def _j(x):
        try:
            return json.dumps(x, ensure_ascii=False) if x else None
        except Exception:
            return None

    return {
        "protagonist_first_appearance_chapter":  a1.get("chapter"),
        "protagonist_first_appearance_position": a1.get("position"),
        "protagonist_first_appearance_context":  a1.get("context"),
        "protagonist_strength_position":         a2.get("strength_position"),
        "protagonist_personality_json":          _j(a2.get("personality_keywords")),
        "protagonist_appearance_detail_level":   a2.get("appearance_detail"),
        "protagonist_age_group":                 a2.get("age_group"),
        "protagonist_contrast_point":            a2.get("contrast_point"),
        "protagonist_cheat_type":                a3.get("type"),
        "protagonist_cheat_source":              a3.get("source"),
        "protagonist_cheat_revealed_chapter":    a3.get("revealed_chapter"),
        "protagonist_cheat_description":         a3.get("description"),
        "protagonist_agency":                    a4.get("mode"),
        "protagonist_first_decision_chapter":    a4.get("first_decision_chapter"),
        "drive_type":                            a5.get("type"),
        "drive_urgency":                         a5.get("urgency"),
        "drive_object":                          a5.get("object"),
        "drive_method":                          a5.get("method"),
        "drive_description":                     a5.get("description"),
        "social_network_json":                   _j(b1.get("characters_in_5ch")),
        "key_relationships_json":                _j(b1.get("key_relationships")),
        "focus_mode":                            b2.get("mode"),
        "character_count_in_5ch":                b2.get("character_count_5ch") or 0,
        "antagonist_first_chapter":              b2.get("antagonist_first_chapter"),
        "antagonist_type":                       b2.get("antagonist_type"),
        "worldview_power_system":                c1.get("power_system"),
        "worldview_time_space":                  c1.get("time_space"),
        "worldview_hybrid_type":                 c1.get("hybrid_type"),
        "worldview_novelty_level":               c1.get("novelty_level"),
        "setting_word_ratio":                    c2.get("word_ratio") or 0.0,
        "setting_unfold_style":                  c2.get("style"),
        "setting_density_first_chapter":         c2.get("first_chapter_density"),
        "core_setting_revealed":                 c2.get("core_revealed"),
        "setting_contrast_point":                c3.get("point"),
        "opening_hook_type":                     d1.get("type"),
        "opening_hook_description":              d1.get("description"),
        "opening_hook_trigger_position":         d1.get("trigger_position"),
        "has_early_face_slap":                   1 if d2.get("has_early_face_slap") else 0,
        "first_face_slap_chapter":               d2.get("first_face_slap_chapter"),
        "pleasure_point_type":                   d2.get("type"),
        "pleasure_point_density":                d2.get("density") or 0,
        "writing_style_keywords_json":           _j(e1.get("keywords")),
        "representative_sentences_json":         _j(e1.get("representative_sentences")),
        "emotional_tone":                        e2.get("dominant"),
        "emotional_volatility":                  e2.get("volatility"),
        "darkness_level":                        e2.get("darkness"),
        "info_disclosure_strategy":              f1.get("strategy"),
        "early_foreshadows_json":                _j(f1.get("early_foreshadows")),
        "revealed_vs_planted_ratio":             f1.get("revealed_vs_planted_ratio") or 0.0,
        "first_arc_concept":                     f2.get("concept"),
        "first_arc_locked_chapter":              f2.get("locked_chapter"),
        "chapter_end_hook_type":                 g1.get("type"),
        "chapter_end_hook_strength":             g1.get("strength"),
    }
